# Mother control surface

Status: design baseline for the new `mother` namespace.

## Normative requirement language

The uppercase keywords `MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, and
`MAY` are the only modal keywords with defined requirement strength in this
document:

- `MUST` / `MUST NOT`: mandatory for a conforming implementation;
- `SHOULD` / `SHOULD NOT`: required unless a documented, reviewable exception
  justifies deviation without weakening a `MUST` requirement;
- `MAY`: explicitly permitted optional behavior.

Explicitly labeled invariants, normative schemas, state-transition tables,
safety-rule sections, and requirement blocks are also normative when written as
declarative contracts; they do not require an artificial modal keyword on every
line.

Lowercase words such as “must,” “should,” and “may” are ordinary non-normative
prose and MUST NOT carry unidentified requirement strength. Documentation lint
MUST reject lowercase modal terms when they are used to express an obligation,
recommendation, or permission in normative Markdown prose outside fenced
examples, quotations, and explicitly marked non-normative text. The lint MUST
evaluate logical prose paragraphs after normalizing Markdown source-line wrapping
and MUST reject malformed modal constructions even when their words are split
across adjacent source lines. Authors SHOULD
use unambiguous descriptive alternatives such as “can,” “might,” or
“recommended” when no requirement is intended.

Descriptive examples, placeholder field names, and conceptual endpoint names are
non-normative unless a surrounding requirement block, normative schema, state
table, invariant, or uppercase modal explicitly makes them mandatory.

## Requirements verification index

This front-matter index is an audit and coverage map, not a second copy of the
requirements. Each summary is non-normative; the linked owning section remains
authoritative. A requirement identifier is a permanent reference handle and
MUST NOT be renumbered or reused after publication. `Status` describes design
readiness only and does not claim that an implementation or test already exists.

| Requirement ID | Summary | Owning section | Verification | Dependency | Status |
| --- | --- | --- | --- | --- | --- |
| `MOTHER-REQ-001` | Normative language and declarative contracts | [Normative requirement language](#normative-requirement-language) | Markdown semantic lint | — | Specified |
| `MOTHER-REQ-002` | Read-only discovery does not mutate authority or infrastructure | [Design goals](#design-goals) | Read-only integration and side-effect inspection | — | Implementable |
| `MOTHER-REQ-003` | Durable state and private identity survive control-container replacement | [Mother durable state and private identity](#mother-durable-state-and-private-identity) | Schema validation and replacement-start recovery test | — | Implementable |
| `MOTHER-REQ-004` | Journals replay from a valid checkpoint to a proven head | [Checkpoint-aware replay](#checkpoint-aware-replay) | Replay corpus, corruption, and checkpoint-selection tests | — | Implementable |
| `MOTHER-REQ-005` | Journal entries and heads have deterministic atomic commit semantics | [Atomic filesystem commit](#atomic-filesystem-commit) | Crash-point and fsync fault-injection tests | — | Implementable |
| `MOTHER-REQ-006` | Operating-system locks, scope ownership, and full-set reservations serialize mutation | [Locking model](#locking-model) | Concurrent-writer, split-reservation, and stale-metadata tests | — | Implementable |
| `MOTHER-REQ-007` | Cross-journal facts retain one owner and one irreversible commit point | [Cross-journal transitions](#cross-journal-transitions) | Replay proof and interrupted-finalize tests | `MOTHER-OPEN-018` for full-set acknowledgement | Implementable locally |
| `MOTHER-REQ-008` | Distributed mutation starts only after a full-network clean-state barrier | [Full-network clean-state barrier](#full-network-clean-state-barrier) | Multi-host disagreement and unreachable-host tests | `MOTHER-OPEN-017` for prospective replicas | Implementable for established replicas |
| `MOTHER-REQ-009` | Rollback frames are armed before mutation and removed only after verified restoration | [Distributed rollback layers](#distributed-rollback-layers) | Interrupted-step, retry, and LIFO restoration tests | — | Implementable |
| `MOTHER-REQ-010` | RPC routing is a typed, reversible distributed resource | [Typed RPC routing resource](#typed-rpc-routing-resource) | Complete-prestate and convergence tests | — | Implementable |
| `MOTHER-REQ-011` | Hub/FDB topology is a typed, reversible distributed resource | [Typed Hub/FDB topology resource](#typed-hubfdb-topology-resource) | Participant convergence and rollback tests | — | Implementable |
| `MOTHER-REQ-012` | `add-node` is one staged distributed lifecycle operation | [Integrated add-node sequence](#integrated-add-node-sequence) | End-to-end lifecycle and rollback proof | `MOTHER-OPEN-017`, `MOTHER-OPEN-018` | Blocked for production |
| `MOTHER-REQ-013` | `remove-node` is one staged distributed lifecycle operation | [Integrated remove-node sequence](#integrated-remove-node-sequence) | End-to-end lifecycle and rollback proof | `MOTHER-OPEN-017`, `MOTHER-OPEN-018` | Blocked for production |
| `MOTHER-REQ-014` | QBFT membership changes use frozen participants, durable receipts, and convergence proof | [Frozen proposal and participant manifest](#frozen-proposal-and-participant-manifest) | Vote, receipt, effective-set, and block-proof tests | — | Implementable beneath blocked production mutation |
| `MOTHER-REQ-015` | Schema and capability negotiation fails closed | [Startup and command preflight](#startup-and-command-preflight) | Compatibility matrix and unsupported-schema tests | — | Implementable |
| `MOTHER-REQ-016` | Recovery transfers the complete transitive object and private-state closure | [Resolved design decisions](#resolved-design-decisions) | Closure traversal, missing-object, and hash tests | — | Implementable |
| `MOTHER-REQ-017` | Replacement-head recovery requires unanimous compatible replica state | [Resolved design decisions](#resolved-design-decisions) | Recovery rehearsal, disagreement, and resumed-writer-fencing tests | — | Implementable through activation |
| `MOTHER-REQ-018` | `sync-state` adopts an already-authoritative generation through a durable pointer transaction | [Control APIs](#control-apis) | Crash-before/after-pointer and stale-candidate tests | — | Implementable |
| `MOTHER-REQ-019` | Projection repair publishes one head-fenced generation atomically | [Control APIs](#control-apis) | Concurrent-head, crash, and mixed-generation tests | — | Implementable |
| `MOTHER-REQ-020` | Mother and guard mutation APIs remain local and use bounded reusable call-runners | [Remote access through Coolify call-runners](#remote-access-through-coolify-call-runners) | Route exposure, idempotency, and runner-crash tests | — | Implementable |
| `MOTHER-REQ-021` | Active operations exclusively own conflicting scopes until their terminal boundary | [Active operation conflict rule](#active-operation-conflict-rule) | Local conflict-matrix, distributed-reservation, and terminal-release tests | — | Implementable |
| `MOTHER-REQ-022` | Network finalization closes rollback at the authoritative journal commit | [Commit and finalize boundary](#commit-and-finalize-boundary) | Pre/post-commit crash and rollback-closure tests | `MOTHER-OPEN-018` | Implementable locally |
| `MOTHER-REQ-023` | Every full-set replica accepts at most one exact successor for an expected head | [Full-set successor reservation and single-successor commit](#full-set-successor-reservation-and-single-successor-commit) | Competing-writer, split-reservation, delayed-request, crash, and cancellation tests | — | Implementable |
| `MOTHER-REQ-024` | Zero-network and prospective replicas use an explicit enrollment transaction | [Remaining open design nodes](#remaining-open-design-nodes) | Empty-network and host-enrollment lifecycle tests | `MOTHER-OPEN-017` | Blocked |
| `MOTHER-REQ-025` | Finalization replication has exact full-set acknowledgement and recovery transitions | [Remaining open design nodes](#remaining-open-design-nodes) | Ack-loss, retry, recovery, and successor-certificate tests | `MOTHER-OPEN-018` | Blocked |

`mother` is the replacement control surface for validator lifecycle operations that
have outgrown `tools/allfather_control.py`. Allfather remains useful reference
material for Coolify API access, private-state loading, guard/probe mechanics,
existing network naming conventions, and the current super-node runtime model,
but it is no longer the lifecycle authority. Mother starts from clean boundaries.

The immediate purpose of Mother is to make network state observable, prepare an
explicit operation, save that operation as the current operation for the affected
scopes, perform the prepared operation exactly as written, and then finalize it
or roll it back. Mother commands MUST NOT mutate live infrastructure during
discovery and MUST NOT borrow a destructive helper from another lifecycle path
merely because it happens to touch the same service.


## Use case: first node, second node, topology handoff

This use case is the reference story for Mother. `add-node` and `remove-node`
are complete user-facing distributed actions. Validator admission/removal,
RPC routing, Hub/FDB topology, and service lifecycle are internal ordered phases
of those actions; the operator does not run separate topology commands.

Goal:

```text
Start with no committed validator topology.
Add the first super-node on coolify-a and make it fully operational.
Add a second super-node on coolify-c and make it fully operational.
Remove coolify-a's node from the network and remove/disable its service.
End with coolify-c as the solo effective network.
```

Prerequisite:

```text
/runtime/state/mother/ exists before the Mother control surface is deployed.
/runtime/state/mother/identity.private.yaml exists inside that durable state root.
```

The Mother state root is the durable contract. The Mother container and API code
are replaceable; authoritative identity, topology, action, rollback, route, guard,
and lock state MUST NOT live only inside the container filesystem.

`identity.private.yaml` is the source of reserved network identity. It contains
the chain facts, officer/admin identity records, validator identity records, node
reservations, routing reservations, and first-genesis material Mother needs
before any service is deployed. The initial secret backend is inline local private
YAML: private-key fields live directly in this file, and any key references are
internal references to records in the same document.

A representative flow:

```text
# 0. Observe the empty or partially empty world. Read-only only.
mother diagnose mainnet

# 1. Add the first node as one distributed action.
mother add-node prep mainnet --node mainneta-super1 --host coolify-a --mode initial
mother add-node do mainnet

# Result before finalize:
#   the service exists and owns its reserved identity;
#   the first validator is active on Mother-owned first-genesis material;
#   host-local canonical RPC routing is correct;
#   Hub/FDB topology is correct on every current node;
#   the action remains fully rollback-capable.

mother add-node finalize mainnet

# 2. Add the second node as one distributed action.
mother add-node prep mainnet --node mainnetc-super1 --host coolify-c --mode soft
mother add-node do mainnet

# Result before finalize:
#   both validator addresses are in the agreed QBFT set;
#   affected RPC routes include the correct eligible backends;
#   every node reports the new Hub/FDB topology;
#   all distributed rollback layers remain active.

mother add-node finalize mainnet

# 3. Remove coolify-a's node as one distributed action.
mother remove-node prep mainnet --node mainneta-super1 --mode soft
mother remove-node do mainnet

# Result before finalize:
#   Hub/FDB topology excludes mainneta-super1 everywhere;
#   RPC routing excludes mainneta-super1;
#   the validator is absent from the agreed QBFT set;
#   the service is detached, disabled, archived, or removed as prepared;
#   the complete distributed removal remains rollback-capable.

mother remove-node finalize mainnet

# Result:
#   coolify-c's mainnetc-super1 is the solo effective network.
```

Before either `add-node prep` or `remove-node prep` can succeed, every expected
Coolify host MUST be reachable, agree on the committed network journal/state,
and prove that it has no unresolved action, active rollback, provisional guard
frame, or conflicting resource lock. The action is allowed to begin only after
that full-network clean-state barrier succeeds.

At every point after `prep` and before `finalize`, `mother diagnose mainnet`
MUST report the current operation ID, stage, distributed participants, owned
scopes, completed checkpoints, any unresolved provisional layer, active rollback
layers, the currently pop-able contiguous stack range, and allowed next
commands. Rollback is generic:

```text
mother rollback mainnet
```

It resolves the active operation from the Mother control surface and unwinds the
distributed durable rollback stack. The operator does not have to identify an
internal validator, RPC, Hub/FDB, or service phase.

The implementation MUST NOT hide an unknown in this use case. If a step
depends on behavior that is not yet designed, the document MUST contain an
explicit `MOTHER-OPEN-*` node before implementation begins.


## Design goals

Mother exists to answer three questions before any change is made:

1. What exists?
2. What state is it actually in?
3. What exact staged plan would move it to the desired state?

A mutating script MUST NOT act before those answers are recorded.

Mother MUST make the following facts distinct at all times:

- **Coolify service topology**: which services exist, on which Coolify hosts, with
  which service UUIDs, names, ports, and volumes.
- **Runtime process topology**: which local guard, Hub, FDB, Besu validator RPC,
  and helper processes are running inside each existing service.
- **Validator identity topology**: which validator address each service owns,
  derived from that service's validator key.
- **QBFT consensus topology**: which validator addresses the chain currently
  accepts according to `qbft_getValidatorsByBlockNumber("latest")`.
- **Lifecycle marker topology**: which admission, removal, handoff, reseal, or
  recovery markers exist locally and whether they are active, stale, complete, or
  contradictory.
- **Mother operation topology**: which prepared operation currently owns a
  network/service/validator scope, which stage it has reached, and whether it MAY
  still be finalized or rolled back.

The Allfather failure mode was treating these topologies as interchangeable.
Mother's first invariant is that they are never interchangeable.


## Mother durable state and private identity

Canonical durable state root:

```text
/runtime/state/mother/
```

Canonical inline private identity backend:

```text
/runtime/state/mother/identity.private.yaml
```

For private-state schema version 1, that path is normative. Implementations MUST
NOT silently substitute `/runtime/state/mother.private.yaml`, another filename,
or another backend. Any future path or backend change requires an explicit,
versioned migration that preserves the journal, replica, and recovery contracts.

The state root is created before the Mother control surface is deployed. It is
the durable source for identities, topology records, action journals, active
rollback stacks, immutable rollback journals, route before-state snapshots,
guard observations, locks, sealed complete network-state records, and network
facts that MUST survive replacement of the Mother container or API
implementation.

The local state root is the active head copy while the operator is running a
Mother control command. Participating machines also keep sealed replica copies of the complete
network state, including any pending distributed action, for crash recovery. Remote replicas can be stale or
newer than the local head copy; the control script MUST run the sealed-state
preflight before it trusts either side.

The Mother container is disposable. Pushing a new Mother compose or replacing the
mounted Mother API code MUST NOT destroy authoritative state. On startup, Mother
MUST rehydrate its view from `/runtime/state/mother/` plus live guard/topology
discovery.

`identity.private.yaml` plays a similar role to the Allfather private file, but
with stricter ownership boundaries:

- it is owned by Mother, not by ad hoc lifecycle scripts;
- it is topology-aware;
- it records reserved node identities before nodes are deployed;
- it stores private key material with restrictive permissions;
- it is not a substitute for the operation ledger;
- it MUST NOT be rewritten opportunistically by probes.

The private identity file SHOULD be readable and writable only by the Mother
control surface. Recommended local permission target is equivalent to `0600` on
Unix-like systems. If the file is copied or backed up, that copy is also private
state.

Minimum normative schema-version-1 contents:

```yaml
schema_version: 1
kind: main_computer.mother.private_state.v1
control_surface:
  id: mother-control-001
  created_at: "..."
networks:
  mainnet:
    chain_id: "<expected-chain-id>"
    genesis:
      source: mother-private
      first_topology_mode: initial
      qbft:
        blockperiodseconds: 2
        epochlength: 30000
      alloc_accounts:
        - ref: "networks.mainnet.wallets.captain"
    wallets:
      deployer:
        address: "0x..."
        private_key: "0x..."
      captain:
        address: "0x..."
        private_key: "0x..."
      o1:
        address: "0x..."
        private_key: "0x..."
      o2:
        address: "0x..."
        private_key: "0x..."
      o3:
        address: "0x..."
        private_key: "0x..."
    validators:
      mainneta-super1:
        address: "0x..."
        private_key: "0x..."
      mainnetc-super1:
        address: "0x..."
        private_key: "0x..."
    nodes:
      mainneta-super1:
        host: coolify-a
        validator_ref: "networks.mainnet.validators.mainneta-super1"
        guard_route_reservation: "..."
        rpc_route_reservation: "..."
        hub_route_reservation: "..."
      mainnetc-super1:
        host: coolify-c
        validator_ref: "networks.mainnet.validators.mainnetc-super1"
        guard_route_reservation: "..."
        rpc_route_reservation: "..."
        hub_route_reservation: "..."
```

The schema MAY evolve only through an explicit versioned migration. Within
`schema_version: 1`, the wallet, validator, and node-reference shape above is
normative; implementations MUST NOT introduce a second validator identity shape.
These ownership rules also apply:

1. Mother reserves validator identity before `add-node`.
2. `add-node` installs reserved identity; it does not invent validator identity.
3. `add-node` activates chain topology, RPC routing, and Hub/FDB topology using
   Mother-owned facts as ordered phases of one action.
4. The first-node genesis is generated from Mother private state, not guessed by
   a running super-node.
5. Public/officer/admin identities are generated before deployment and recorded
   in private state.
6. Operation records MAY refer to secrets in private state, but SHOULD NOT copy
   raw private key material into non-secret operation ledgers.

### Resolved design decisions

The following items are no longer unknowns.

`MOTHER-DESIGN-001: private-state-owned-node-identity`

Mother owns planned node identity. A node's validator key, validator address,
officer/admin addresses, and route reservations are generated or reserved before
service deployment and recorded in `/runtime/state/mother/identity.private.yaml`.
`add-node` installs the reserved identity. It does not generate an identity as a
side effect of starting a service.

`MOTHER-DESIGN-002: standby-removes-public-routing`

Standby is the safe private phase used while `add-node` prepares a node and while
`remove-node` detaches one. A standby service is not in public RPC routing,
public Hub routing, aggregate public routing, or QBFT validator topology. Moving
a service into standby removes it from public routing. Leaving standby does not
itself publish routes; the enclosing node action publishes or withdraws routes
only through its ordered RPC and Hub/FDB phases.

`MOTHER-DESIGN-003: mother-owned-first-genesis`

The first network genesis is generated before Mother deploys or before the first
node action. `add-node --mode initial` installs the Mother-owned first-genesis
material when adding the first node to an empty topology.

`MOTHER-DESIGN-004: inline-local-private-yaml-secret-backend`

`MOTHER-OPEN-001: exact-secret-backend` is resolved as inline local private YAML.
The stable public durable contract is the Mother state root:

```text
/runtime/state/mother/
```

The private-state backend for the first implementation is:

```text
/runtime/state/mother/identity.private.yaml
```

It stores private identity material directly alongside derived public addresses.
Mother private state MAY use internal references such as
`networks.mainnet.wallets.captain` or
`networks.mainnet.validators.mainneta-super1`, but those references resolve
inside the same YAML document. They do not point to Vault, KMS, Docker secrets,
a mounted secret directory, a host secret store, or another external backend
unless a later schema version explicitly introduces that backend.

The canonical wallet and validator identity shape is:

```yaml
networks:
  mainnet:
    wallets:
      deployer:
        address: "0x..."
        private_key: "0x..."
      captain:
        address: "0x..."
        private_key: "0x..."
      o1:
        address: "0x..."
        private_key: "0x..."
      o2:
        address: "0x..."
        private_key: "0x..."
      o3:
        address: "0x..."
        private_key: "0x..."
    validators:
      mainneta-super1:
        address: "0x..."
        private_key: "0x..."
```

If an operation record needs to name a key, it records a private-state reference,
not a raw private key. For example:

```yaml
validator_key_ref: "networks.mainnet.validators.mainneta-super1"
governance_office_key_refs:
  - "networks.mainnet.wallets.captain"
  - "networks.mainnet.wallets.o1"
  - "networks.mainnet.wallets.o2"
  - "networks.mainnet.wallets.o3"
```

The resolver loads `/runtime/state/mother/identity.private.yaml`, follows the internal
reference, verifies that the derived address matches the recorded address, and
passes the private key only to the component that MUST sign or deploy. Node
actions MAY create, delete, or repair services and routes, but they MUST NOT
delete, regenerate, or rotate these private-state identity records unless the
operator explicitly requests identity rotation.

`MOTHER-DESIGN-005: route-gated-standby-runtime`

`MOTHER-OPEN-002: standby-hub-runtime-behavior` is resolved as internal-only,
route-gated standby. A standby service MAY keep internal guard/runtime processes
available for diagnostics and recovery, but public Traefik routes MUST NOT point
at it. Entering standby captures the complete previous route state in an
`armed-provisional` frame before route mutation. That frame is promoted into the
rollback-stack projection only after route withdrawal is freshly verified.
Leaving standby does not publish routes by itself; the enclosing `add-node` or
`remove-node` action commits routing through its typed RPC and Hub/FDB phases.

`MOTHER-DESIGN-006: api-first-guard-runtime-control`

`MOTHER-OPEN-003: guard-runtime-api` is resolved as API-first runtime control.
High-level Mother actions are decomposed into ordered calls against the Mother
control surface and per-node guard endpoints. Runtime topology changes MUST NOT
depend on replacing compose files.

Compose MAY provision or replace the disposable service shell, but runtime
mutations are API operations. The guard API MUST expose primitives that can
capture and restore complete declared prestate. Prestate restoration MUST be
idempotent; forward primitives SHOULD also be idempotent where practical. The
baseline endpoint set includes:

```text
POST /guard/v1/prestate/capture
PUT  /guard/v1/identity/state
PUT  /guard/v1/node-runtime/state
PUT  /guard/v1/qbft/config
POST /guard/v1/qbft/validator-membership/vote
GET  /guard/v1/qbft/validator-membership/receipts/<receipt-id>
PUT  /guard/v1/validator-rpc/state
PUT  /guard/v1/rpc-routing/state
PUT  /guard/v1/hub-fdb/state
GET  /guard/v1/topology/state
POST /guard/v1/prestate/restore
GET  /guard/v1/prestate/<frame-id>
POST /guard/v1/assertions/verify
```

The typed prestate contract is defined by `MOTHER-DESIGN-012`; the executable
assertion contract is defined by `MOTHER-DESIGN-013`; distributed RPC and
Hub/FDB resource semantics are defined by `MOTHER-DESIGN-015`; guard-mediated
soft QBFT membership is defined by `MOTHER-DESIGN-018`.

Before every mutating guard or routing API call, Mother MUST identify the full
mutation scope, capture the complete current prestate for that scope, and durably
arm a provisional rollback frame that can restore that prestate. The frame
defines a desired prior state and verification contract, not merely an inverse
command. It is promoted onto the active rollback stack only after the forward
desired state is freshly verified and the promotion event is durably committed.
Provisional frames, the active rollback stack, and the immutable rollback journal
MUST all be inspectable through the Mother API.

`MOTHER-DESIGN-007: disposable-mother-container-durable-state-root`

The Mother container and Mother API implementation are replaceable. Operators MAY
push a new Mother compose or install updated Mother API code whenever needed, but
authoritative Mother state MUST live under `/runtime/state/mother/`, not inside
the container filesystem. Mother startup MUST validate the state root, load
identity, action, rollback, route, topology, guard, lock, and version records,
then reconcile those records with live guard/topology discovery.

Rollback stacks are required for topology/runtime mutations, not for ordinary
Mother container replacement. The safety condition for replacing the Mother
container is that the new implementation understands the mounted state schema or
refuses mutating actions until an explicit migration is performed.

`MOTHER-DESIGN-008: coolify-mediated-local-call-runner-transport`

Mother and guard mutation APIs are local-only control APIs. They MUST bind to
localhost or a private host/container network and MUST NOT be published through
public Traefik routes. Public routes are for user-facing Hub/RPC traffic, not for
runtime mutation endpoints.

Remote operator access to Mother is mediated by Coolify/Allfather bootstrap
access. The Coolify API is used to create or invoke one stable private
call-runner service on each target Coolify host. The service identity is reused
for sequential operator requests so ordinary operation does not accumulate one
temporary service per call.

The runner executes a structured local HTTP call into the Mother or guard API,
records transport evidence, and returns to an idle or stopped reusable state
after a terminal request. The initial concurrency limit is one active request
per runner service. A second request MUST wait or fail with `runner-busy`; it
MUST NOT create another ordinary runner service on the same host.

The runner is reusable transport but never operation authority. It MAY be
stopped, restarted, manually killed, quarantined, or explicitly replaced without
corrupting Mother state. It MUST NOT own authoritative topology, identity,
operation, rollback, route, request-result, or lock state. Killing the runner
can lose an in-flight transport response, but it MUST NOT erase a request or
Mother operation once the target local API has durably accepted it. The operator
MUST be able to recover by reading durable request status, Mother operation
records, participant receipts, and idempotency results from
`/runtime/state/mother/`.

A crash, timeout, lost response, or ambiguous result MUST NOT automatically
delete the runner service. The existing service and its Coolify logs remain
available for inspection. After the request is reconciled, the same service is
restarted or reset and reused. A replacement runner MAY be created only after
the existing service is explicitly quarantined or removed; replacement is not
the normal per-request path.

The runner MUST NOT be treated as a general public shell. Its normal contract is
a structured local-call envelope containing at least request identity, target,
method, path, body, and idempotency key. The runner MAY call only approved
local/private Mother or guard endpoints.

The Coolify API credential is the authorization boundary for placing and
executing that runner. Once the Coolify-authorized runner is executing on the
private host, Mother and guard endpoints do not require a second application
credential. Local host access, access to the private container network, or the
ability to launch arbitrary workloads on that host is treated as complete host
compromise and is outside Mother's threat model.

This does not permit public exposure. Mother and guard endpoints remain
local/private-only and MUST NOT receive public Traefik routes. The Coolify API
credential remains operator-side control-plane material and MUST NOT be copied
into Mother journals, rollback frames, replicated state, participant receipts,
or ordinary command output.


`MOTHER-DESIGN-009: active-local-head-with-sealed-network-replicas`

The active Mother authority is the local head node: the machine where the
operator is running the Mother control script. The local head owns operator
intent, prepares operations, drives `do`/`finalize`/`rollback`, and is the only
writer allowed to commit global network-journal transitions during that command.

Remote Coolify hosts are not independent topology authorities. They are
execution targets and sealed-state replicas. They MAY hold local provisional
operation state for work that affects only that host, but they MUST NOT
independently advance finalized topology or replicated pending-action state.

Every network has a sealed complete network-state record replicated to the
Coolify hosts named by that record. A durable journal transition MAY describe
either finalized topology or a still-reversible pending distributed action.
`committed` means durably present in the journal lineage; it does not by itself
mean that the operator has finalized the topology. A seal records at least:

```yaml
network_key: mainnet
finalized_topology_epoch: 42
state_hash: "sha256:..."
previous_state_hash: "sha256:..."
journal_head_sequence: 142
journal_head_hash: "sha256:..."
active_checkpoint_id: "checkpoint-..."
active_checkpoint_hash: "sha256:..."
replica_set_hash: "sha256:..."
active_writer_operation_id: "add-node-mainneta-super4-001"
last_successor_certificate_hash: "sha256:..."
pending_action_id: "add-node-mainneta-super4-001"
pending_action_phase: "rpc-routing-verified"
private_state_kind: "main_computer.mother.private_state.v1"
private_state_generation: 12
private_state_hash: "sha256:..."
private_recovery_manifest_hash: "sha256:..."
recovery_closure_manifest_hash: "sha256:..."
replica_hosts:
  - coolify-a
  - coolify-b
  - coolify-c
excluded_hosts: []
sealed_at: "..."
sealed_by: "local-head:<machine-id>"
last_finalized_action_id: "operation-..."
schema_version: "mother.network-state.v1"
```

`replica_hosts` is the exact expected replica set for that sealed epoch. It is
not an advisory inventory list. Its canonically serialized host identities
produce `replica_set_hash`; response order, discovery order, DNS order, and
reachability order do not. A host remains part of the expected replica set until
an explicit reseal commits a new replica set that excludes it.
`active_writer_operation_id` is null when no distributed writer reservation is
attached to the committed pending state. `last_successor_certificate_hash`
identifies the exact full-set certificate that authorized the current journal
head.

Before any Mother command talks to or mutates a remote network, the local control
script MUST run a sealed-state and journal preflight:

1. load the local complete network-state document and active journal lineage;
2. replay the active journal lineage and prove that the reconstructed state,
   including finalized topology and any pending distributed action, exactly
   matches the local network-state document;
3. load and validate the local private-state document, private-state metadata,
   and private-recovery manifest referenced by the reconstructed network state;
4. load the expected `replica_hosts` from that reconstructed state;
5. query every expected replica host for its journal head, active checkpoint,
   replayed state hash, finalized topology epoch, pending-action metadata,
   private-state schema/generation/hash, private-recovery manifest hash, and
   durable successor-reservation state for the exact expected head;
6. stop before normal mutation if any expected replica host is unreachable,
   cannot replay its journal, cannot validate its private recovery material, or
   does not return usable network-state and private-state metadata;
7. require every expected replica and the local head to agree on the active
   checkpoint, journal head sequence/hash, finalized topology epoch, pending
   action identity/phase, complete state hash, private-state schema/generation/
   hash, private-recovery manifest hash, expected replica-set hash, and any
   active writer-reservation owner or successor claim;
8. if every expected replica agrees and the local head is stale, stop ordinary
   mutation and direct the operator to staged `sync-state`. That operation MUST
   adopt the complete frozen generation, including private-state and recovery
   objects referenced by its verified closure, and MUST commit only through the
   local active-generation pointer switch;
9. if journals diverge, journal replay disagrees with a network-state document,
   a required record is missing, equal finalized epochs have different complete
   state hashes, private-state metadata differs, pending-action metadata differs,
   or live facts contradict the reconstructed state, refuse normal mutation and
   require remediation or an explicit rectification/reseal operation.

Normal mutation uses full expected-replica-set agreement, not an automatic
majority quorum. For example, if `coolify-a` and `coolify-c` agree but
`coolify-b` is unreachable while the current state still lists all three hosts,
Mother MUST NOT silently proceed with two of three.

The operator has exactly two availability choices when an expected replica is
unreachable:

1. restore reachability to that host and rerun preflight; or
2. explicitly reseal the network with a new replica set that excludes the
   missing host.

Resealing without a missing host is a network-visible recovery action. It MUST
create a new topology epoch and state hash, record the removed host and reason,
write the new state, journal records, and current private-recovery bundle to
every remaining expected replica, require matching non-secret hash receipts, and
mark the previous seal as superseded rather than deleting it. For example:

```yaml
network_key: mainnet
finalized_topology_epoch: 43
previous_state_hash: "sha256:old..."
state_hash: "sha256:new..."
replica_hosts:
  - coolify-a
  - coolify-c
excluded_hosts:
  - host: coolify-b
    reason: "unreachable during operator-approved reseal"
    excluded_at_epoch: 43
```

A host excluded by reseal cannot automatically resume replica participation when
it becomes reachable again. Its older state and journal head are stale by
definition. It MUST be refreshed from the current committed state, current
private-state generation, and complete private-recovery bundle, then explicitly
re-included through a replica-rejoin or reseal operation that creates another
new epoch.

Wall-clock modified time MAY be used as an operator hint, but it is not the
authority. The authority is the replayed journal lineage, sealed epoch, state
hash, active checkpoint, and expected replica set. `modified_at` fields SHOULD
be recorded for diagnostics, but normal mutation MUST compare the cryptographic
and sequence metadata.

Host-local capture details, temporary files, retry logs, and transient health
samples remain in the action, rollback, and participant journals. The replicated
network journal nevertheless records the network-scoped action as soon as the
full-network barrier is crossed. Its complete state contains the last finalized
topology plus the current pending distributed action.

Every meaningful distributed transition is appended and replicated as it occurs,
including:

```text
writer reservation attached and pending action opened
successor certificate accepted
participant set accepted
distributed prestate layer armed
validator membership verified
RPC routing verified
Hub/FDB topology verified
remediation required
partial or complete rollback verified
ready to finalize
pending action finalized or rolled back
```

The global entry MAY reference detailed participant receipts and action/rollback
journal entries by stable journal identity, sequence, entry hash, and state hash
rather than duplicating every local byte. Transient observations that do not
change the action state need not become global entries.

This keeps every replica aware of both accepted intent and reversible physical
reality:

```text
finalized topology
  + replicated pending action
  + applied/verified phase and rollback references
  -> complete sealed network state
```

For removal, public route withdrawal MAY happen early for safety. The
route-withdrawn result is therefore committed immediately as a pending-action
transition and replicated to every expected host. It does not become finalized
topology until `finalize`. Its complete prestate and restore attempts remain in
the action-specific rollback journals, referenced by the pending network state.

`reseal` is an explicit recovery operation, not a normal sync. It is used when
remote replicas disagree, an expected replica is unreachable and MUST be
excluded, an excluded host MUST be re-included, the network is wedged, a sealed
state cannot be proven, or the operator intentionally chooses a new committed
state from live facts. Reseal MUST inspect local and remote journals and states,
inspect live guards/topology/routes, write a new epoch and state, push the
resulting journal/state lineage to all replicas in the new set, and preserve
superseded conflicting history rather than silently deleting it.


`MOTHER-DESIGN-010: replayed-journal-with-authoritative-checkpoints`

`MOTHER-OPEN-004: sealed-state-format` is resolved as one complete replicated
network-state document plus an append-only, hash-chained journal that is replayed
during every network preflight. The complete state includes both finalized
topology and any pending distributed action.

Recommended per-network durable layout:

```text
/runtime/state/mother/networks/<network>/
  committed-state.json
  journal/
    metadata.json
    head.json
    entries/
      000000000001.json
      000000000002.json
      ...
  archive/
    superseded-lineages/
```

Checkpoint records are immutable entries inside `journal/entries/`; they are not
maintained in a separate mutable checkpoint store. The common journal storage,
locking, commit, and replay rules are defined by `MOTHER-DESIGN-014`.

`committed-state.json` contains the complete current replicated network state
needed by normal operation. Its historical filename is retained, but `committed`
means durably journaled rather than necessarily finalized. The document contains
at least:

```text
finalized topology and finalized topology epoch
zero or one network-scoped pending distributed action
pending desired topology
currently applied and verified distributed phases
participant and receipt references
active writer operation and last accepted successor-certificate hash
rollback availability and remediation status
expected replica set and canonical replica-set hash
```

It is a persisted checkpoint, not an independently trusted authority. The
journal is the canonical history of all durable network-state transitions. The
state document is valid only when deterministic journal replay produces exactly
the same canonical state and state hash.

The journal contains transitions such as:

- pending distributed action opened or phase advanced;
- validator membership applied, restored, or finalized;
- replica-set changes;
- canonical Hub or RPC route changes;
- Hub/FDB topology changes;
- remediation and rollback progress that changes network-scoped action state;
- contract deployment or governance-office changes;
- zero-node and first-node transitions;
- finalization, reseal, rectification, and authoritative-checkpoint events.

Host-local temporary files, raw retry logs, transient health observations,
locks, call-runner transport records, and full rollback payloads do not belong in
the global journal. Their durable action, rollback, or participant journal
entries are referenced from the network journal when they affect the replicated
pending-action state.

Each ordinary journal entry records at least:

```yaml
kind: main_computer.mother.journal_entry.v1
network_key: mainnet
sequence: 142
action_id: "operation-..."
operation: "add-node"
state_class: "pending-action"
expected_head_id: "mother-head-..."
expected_head_epoch: 7
expected_journal_sequence: 141
expected_journal_hash: "sha256:..."
expected_replica_set_hash: "sha256:..."
prepared_intent_hash: "sha256:..."
successor_certificate_hash: "sha256:..."
previous_entry_hash: "sha256:..."
previous_state_hash: "sha256:..."
changes: []
resulting_state_hash: "sha256:..."
entry_hash: "sha256:..."
committed_at: "..."
```

Every expected replica stores the complete network-state document, journal
metadata, the committed head, all entries retained after the replay base, and
the checkpoint entries needed to reconstruct the active lineage. Replica
preflight does not merely compare copied state files; each replica MUST open and
replay its journal through the common journal engine and report the resulting
hash.

On every command that reads a network, including one that can mutate it, Mother
MUST:

1. read a stable committed journal head;
2. walk backward through that committed lineage until it reaches the newest
   valid checkpoint entry;
3. verify the checkpoint entry, load its complete state, and verify its state
   hash;
4. replay every collected later entry in forward sequence order;
5. verify sequence continuity, entry hashes, previous-entry links, and
   previous/resulting-state hashes;
6. reconstruct the complete current state;
7. compare the reconstructed state with `committed-state.json`;
8. compare the local checkpoint, journal head, replay result, and complete
   network state with every expected remote replica.

If the `committed-state.json` network-state projection and journal replay disagree, normal mutation is
blocked. The operator MUST select an explicit rectification path. Supported
conceptual paths are:

```text
rebuild-committed-state-from-journal
restore-journal-from-agreed-remote
select-journal-lineage
force-authoritative-checkpoint-from-live-facts
```

Mother MUST show the conflicting local and remote heads, reconstructed hashes,
complete network-state hashes, and relevant live facts before the operator chooses.
Rectification MUST NOT silently pick a winner.

An authoritative rectification checkpoint is the recovery mechanism for a state
that cannot be reconciled through normal replay. It is an explicit
operator-approved journal event containing a complete network state and enough
evidence to explain why the earlier lineage was superseded. It records at least:

```yaml
kind: main_computer.mother.authoritative_checkpoint.v1
journal_id: "network:mainnet"
network_key: mainnet
checkpoint_id: "checkpoint-..."
checkpoint_kind: authoritative-rectification
sequence: 143
previous_entry_hash: "sha256:..."
reason: "operator-approved recovery from divergent journal lineages"
created_by: "local-head:<machine-id>"
created_at: "..."
replica_hosts:
  - coolify-a
  - coolify-c
supersedes:
  journal_entries_through: 142
  prior_lineage_heads: []
checkpoint_state: {}
checkpoint_state_hash: "sha256:..."
resulting_state_hash: "sha256:..."
previous_checkpoint_hash: "sha256:..."
entry_hash: "sha256:..."
```

The authoritative checkpoint does not edit or erase prior journal records.
Instead, it supersedes them for active-state reconstruction. Future replay uses
the checkpoint's complete state as the new baseline and applies only later
entries. Earlier journal entries remain preserved as forensic history under the
superseded lineage, but they no longer determine active state.

The active lineage after a checkpoint is therefore:

```text
authoritative checkpoint
  -> journal entry N+1
  -> journal entry N+2
  -> current committed-state.json
```

The checkpoint and reconstructed state MUST be replicated to every host in its
declared `replica_hosts` set. Normal mutation remains blocked until every listed
replica reports the same checkpoint hash, journal head, replayed state hash, and
committed-state hash.

A forced checkpoint is allowed only through explicit rectification/reseal
workflow. Ordinary `add-node`, `remove-node`, and route reconciliation commands
MUST NOT create one automatically.

Conceptual rectification command forms:

```text
python tools/mother/mother.py reseal-state prep mainnet \
  --rectification rebuild-committed-state-from-journal \
  --reason "state file differs from valid journal"

python tools/mother/mother.py reseal-state prep mainnet \
  --rectification restore-journal-from-agreed-remote \
  --source-host coolify-a \
  --reason "local journal damaged"

python tools/mother/mother.py reseal-state prep mainnet \
  --rectification select-journal-lineage \
  --source-host coolify-a \
  --reason "remote lineages diverged"

python tools/mother/mother.py reseal-state prep mainnet \
  --rectification force-authoritative-checkpoint-from-live-facts \
  --reason "no stored lineage matches verified live state"
```

Names MAY change, but the behavior MUST NOT: replay happens before trust,
unreconcilable disagreement requires operator choice, and a forced baseline is
recorded as a new authoritative checkpoint rather than as edits to old history.


`MOTHER-DESIGN-011: prestate-first-rollback-with-rollback-journal`

`MOTHER-OPEN-005: crash-and-ambiguous-step-recovery` is resolved by treating
the complete prestate of each declared mutation scope as the unit of recovery.

Before a mutating substep starts, Mother MUST:

1. identify every file, process, route, topology record, or remote runtime fact
   the substep is allowed to change;
2. read and validate the complete current prestate for that scope;
3. record the prestate, its canonical hash, the owned scope/generation, the
   restore operation, and the rollback verification contract in a durable
   rollback frame;
4. commit that frame as `armed-provisional` in the action journal;
5. only then dispatch the forward mutation.

An armed provisional frame is executable recovery state, but it is not yet a
completed item on the active rollback stack. A rollback frame describes the
desired prior state. It MUST NOT rely only on an inverse verb such as
`start -> stop` or `add -> remove`, because an inverse verb might not recreate the
exact previous configuration. A conceptual frame is:

```yaml
frame_id: "rollback-0003"
operation_id: "operation-..."
step_id: "publish-mainnet-rpc-route"
status: armed-provisional
scope: "route:mainnet-rpc:coolify-a"
target_generation: 17
prestate:
  exists: true
  canonical_hash: "sha256:..."
  complete_value: {}
restore:
  kind: "route.restore-complete-prestate"
  payload_ref: "rollback-prestate/rollback-0003.json"
verification:
  expected_prestate_hash: "sha256:..."
```

After the forward mutation, Mother freshly verifies the complete postcondition
and active invariant set. Mother MUST NOT commit the following single
action-journal transition until that verification succeeds:

```text
step-applied-verified-and-promoted
```

That transition changes the frame from `armed-provisional` to a completed,
executable item in the active rollback-stack projection. Mother MUST NOT
continue to the next forward step before that promotion is durable. For a distributed
step, promotion occurs only when every required participant frame is armed and
every required participant has freshly verified the same desired resulting
generation.

If the forward mutation fails, is interrupted, returns an ambiguous result, or
cannot pass the required assertions, its frame remains `armed-provisional`. The
action enters `remediation-required`; the failed frame is not represented as a
completed rollback-stack item. The operator MAY retry/resume using the same
frame, or restore and close the provisional frame before rolling back completed
stack layers. Mother MUST NOT recapture prestate over a partial result and call
that partial result the new prestate.

Rollback remains available from successful `prep` until the operation's
documented irreversible commit point. For ordinary network mutations, that point
is the authoritative `pending-action-finalized` network-journal commit. For
`sync-state`, that point is the atomic active-generation pointer switch. After
the applicable irreversible commit point, reversing the result requires a new
prepared action with its own prestate and rollback stack.

Rollback of promoted frames processes the active stack in strict LIFO order:

1. peek at the top frame without removing it;
2. mark a restore attempt in progress;
3. apply the frame's complete prestate restore;
4. verify the actual target state against the recorded prestate hash and
   rollback postconditions;
5. append the attempt and verification result to the immutable rollback journal;
6. remove the frame from the active stack only after restoration is
   `restored-verified`;
7. continue to the next frame only after the current top frame has been removed.

If restoration fails, is interrupted, or cannot be verified, the frame remains
at the top of the active stack. The failed attempt is appended to the rollback
journal, lower frames are not processed, the action remains rollback-capable,
and rerunning rollback retries the same idempotent restore.

An unresolved provisional frame is logically above the promoted stack. Mother
MUST NOT pop any completed layer until it has first either:

```text
retry/resume and promote the provisional frame after successful verification
or
restore its complete prestate, verify it, journal provisional-restored-verified,
and close it without promotion
```

Finalization also preserves rollback history. It is forbidden while any
provisional frame remains unresolved. Before clearing the active stack,
`finalize` MUST append a `frame-close-prepared` record for every unused promoted
frame to the rollback journal and verify that journal head is durable. For a
network-scoped action it then commits `finalization-prepared` in the action
journal and `pending-action-finalized` in the network journal using the
cross-journal protocol in `MOTHER-DESIGN-014`. Only the network-journal
finalization commit makes the referenced frames permanently non-executable; the
stack projection is cleared afterward.

Each action therefore has four related durable views:

```text
forward action journal
  prepared steps, provisional arming, dispatches, verification, promotion,
  checkpoints, remediation decisions, and finalization

provisional frame set
  armed frames for the one unresolved forward layer, not yet promoted

active rollback stack
  only successfully verified and promoted rollback layers

immutable rollback journal
  restore attempts, failed attempts, verified restorations, provisional closure,
  and promoted frames closed by finalize
```

The provisional set and active stack are replayable projections. The journals
are authoritative. The rollback journal is not the global committed-state
journal. A successful rollback can produce a new network-visible committed
transition, but frame contents and restore-attempt history remain in the
action-specific rollback journal.

Recommended action-local durable layout:

```text
/runtime/state/mother/actions/<operation-id>/
  action-journal/
    metadata.json
    head.json
    entries/
  rollback-journal/
    metadata.json
    head.json
    entries/
  provisional/
    <step-id>/
      <frame-id>.json
  rollback-stack.json
  prestate/
    <frame-id>.json
  summary.json
```

`rollback-stack.json`, the provisional-frame summary, and `summary.json` are
replayable projections. Arming, promotion, restore attempts, verified
restoration, provisional closure, and closure by finalize are committed through
the action and rollback journals before those projections are changed.

The Mother API MUST expose all three operational records:

```text
GET /v1/operations/<operation-id>/provisional-frames
GET /v1/operations/<operation-id>/rollback-stack
GET /v1/operations/<operation-id>/rollback-journal
```


`MOTHER-DESIGN-012: typed-guard-prestate-contract`

`MOTHER-OPEN-008: exact-guard-endpoint-schemas` is resolved as a typed,
prestate-first guard contract.

A guard mutation is never a single opaque command. It has two explicit control
steps:

```text
capture-and-arm complete prestate
apply typed desired-state mutation using that armed frame
```

The capture step MAY write Mother control metadata, but it MUST NOT change the
live resource being protected. The apply step MUST refuse to begin unless the
referenced rollback frame exists, is durable, remains `armed-provisional`, is
owned by the current action, and still matches the target's current generation
and prestate hash.

The baseline local guard surface is:

```text
POST /guard/v1/prestate/capture
PUT  /guard/v1/identity/state
PUT  /guard/v1/node-runtime/state
PUT  /guard/v1/qbft/config
POST /guard/v1/qbft/validator-membership/vote
GET  /guard/v1/qbft/validator-membership/receipts/<receipt-id>
PUT  /guard/v1/validator-rpc/state
PUT  /guard/v1/rpc-routing/state
PUT  /guard/v1/hub-fdb/state
GET  /guard/v1/topology/state
POST /guard/v1/prestate/restore
GET  /guard/v1/prestate/<frame-id>
```

Endpoint names MAY gain resource-specific subpaths, but they MUST NOT lose the
capture/apply/restore semantics.

A prestate-capture request contains a common envelope:

```json
{
  "schema": "mother.guard.prestate-capture.v1",
  "action_id": "add-node-mainneta-super2-001",
  "step_id": "install-reserved-identity",
  "request_id": "request-001",
  "idempotency_key": "idem-capture-001",
  "network": "mainnet",
  "target": {
    "host": "coolify-b",
    "cell_id": "mainneta-super2",
    "resource": "identity"
  },
  "mutation_kind": "identity.install-reserved",
  "declared_scope": [
    "identity.files",
    "identity.permissions",
    "identity.validator-address",
    "identity.secret-mounts"
  ],
  "desired_state_hash": "sha256:..."
}
```

The guard MUST reject the request if the declared scope is incomplete for the
requested mutation kind. A successful response returns the complete immutable
rollback frame:

```json
{
  "ok": true,
  "schema": "mother.guard.prestate-capture-result.v1",
  "action_id": "add-node-mainneta-super2-001",
  "step_id": "install-reserved-identity",
  "request_id": "request-001",
  "idempotency_key": "idem-capture-001",
  "frame_id": "rollback-0002",
  "mutation_scope": [
    "identity.files",
    "identity.permissions",
    "identity.validator-address",
    "identity.secret-mounts"
  ],
  "prestate": {},
  "prestate_hash": "sha256:...",
  "prestate_generation": 7,
  "status": "armed-provisional"
}
```

The guard stores the full frame under the durable Mother state root, for
example under `/runtime/state/mother/provisional/<action-id>/<step-id>/`, before
returning success. Mother commits the same frame or a content-addressed reference
to it as `frame-armed-provisional` in the action journal before dispatching the
forward mutation. `prep` records the planned frame scope and restore contract;
capture turns that plan into executable provisional recovery state. It does not
yet add the frame to the active rollback stack. Promotion, retry, restoration,
and cleanup follow `MOTHER-DESIGN-016`.

Every typed mutation request references the armed frame:

```json
{
  "schema": "mother.guard.identity-state.v1",
  "action_id": "add-node-mainneta-super2-001",
  "step_id": "install-reserved-identity",
  "request_id": "request-002",
  "idempotency_key": "idem-apply-002",
  "frame_id": "rollback-0002",
  "expected_prestate_hash": "sha256:...",
  "expected_generation": 7,
  "desired_state": {}
}
```

Before applying, the guard MUST re-read the target and prove that its current
generation and canonical state hash still match the armed frame. If they do not,
the guard returns `prestate-mismatch` or `generation-mismatch` and performs no
live mutation.

A successful typed mutation response contains:

```json
{
  "ok": true,
  "schema": "mother.guard.mutation-result.v1",
  "action_id": "add-node-mainneta-super2-001",
  "step_id": "install-reserved-identity",
  "request_id": "request-002",
  "idempotency_key": "idem-apply-002",
  "frame_id": "rollback-0002",
  "mutation_scope": [
    "identity.files",
    "identity.permissions",
    "identity.validator-address",
    "identity.secret-mounts"
  ],
  "prestate_hash": "sha256:...",
  "prestate_generation": 7,
  "resulting_state": {},
  "resulting_state_hash": "sha256:...",
  "resulting_generation": 8,
  "verification": {
    "ok": true,
    "checks": []
  },
  "status": "applied-awaiting-mother-verification"
}
```

The response MAY summarize the prestate, but it does not replace the durable
rollback frame and does not itself promote that frame. Mother MUST freshly run
the complete required guard set. Mother MUST NOT commit
`step-applied-verified-and-promoted` before that verification succeeds. After the
commit, the frame appears on the active rollback stack until rollback restores it
or the operation's irreversible commit point closes it.
Repeating a capture or apply request with the same idempotency key and identical
request hash MUST return the same frame or result; reusing the key with different
content MUST fail.

Rollback uses:

```text
POST /guard/v1/prestate/restore
```

with the frame ID, expected current generation, and expected current ownership.
The restore operation applies the complete recorded prestate, verifies the
restored state hash and resource-specific postconditions, and returns
`restored-verified`. Repeating the same restore MUST be safe. For a promoted
frame, Mother journals every restore attempt and removes the frame from the
active stack only after a durable `restored-verified` result. For an unpromoted
provisional frame, the same verified restore closes the provisional frame
without ever adding it to the completed stack.

The guard MUST use structured error codes. Baseline codes are:

```text
unsupported-schema
unsupported-capability
scope-incomplete
scope-busy
prestate-mismatch
generation-mismatch
frame-missing
frame-not-active
invalid-transition
verification-failed
partial-apply
restore-failed
```

The guard MUST NOT accept arbitrary shell as a substitute for a typed mutation
contract. Resource-specific payloads remain typed, and each endpoint MUST define
its complete mutation scope, canonical hashing rules, generation rules, desired
state, verification checks, and restore checks.

The route controller follows the same prestate-first pattern. Its typed
RPC-routing and Hub/FDB desired-state resources, distributed participant rules,
and verification contract are defined by `MOTHER-DESIGN-015`. The soft QBFT
membership endpoint uses the same provisional-frame discipline, but restores
consensus membership through the compensating distributed transition defined by
`MOTHER-DESIGN-018`.


`MOTHER-DESIGN-013: evidence-backed-full-guard-assertions`

Guard flags are evidence-backed executable assertions. They are not writable
booleans, cached intent, or lifecycle markers. Mother MAY ask a guard to verify
an assertion, but Mother and other callers MUST NOT set an assertion to
`true`.

The baseline assertion surface is:

```text
POST /guard/v1/assertions/verify
```

A request names the exact assertion set and scope that MUST be evaluated:

```json
{
  "schema": "mother.guard.assertion-request.v1",
  "action_id": "add-node-mainneta-super2-001",
  "step_id": "establish-standby-runtime",
  "network": "mainnet",
  "target": {
    "host": "coolify-b",
    "cell_id": "mainneta-super2"
  },
  "assertions": [
    "identity.matches-reservation",
    "identity.permissions-secure",
    "runtime.is-safe-standby",
    "routes.public-absent"
  ]
}
```

For every requested assertion, the guard MUST execute the assertion's versioned
verifier against the underlying resources at request time. It MAY inspect files,
permissions, mounts, process state, container state, ports, configuration
hashes, runtime responses, local route definitions, finalized on-chain state
through a typed contract reader, and other typed evidence owned by that
verifier. A successful request does not mean every assertion is true; it means
the guard completed the requested evaluations and returned an evidence-backed
result for each one.

A representative result is:

```json
{
  "schema": "mother.guard.assertion-result.v1",
  "verified_at": "2026-07-23T21:00:00Z",
  "scope": {
    "network": "mainnet",
    "host": "coolify-b",
    "cell_id": "mainneta-super2"
  },
  "results": [
    {
      "name": "runtime.is-safe-standby",
      "verifier": "mother.guard.runtime-is-safe-standby.v1",
      "result": true,
      "dependencies": {
        "node-runtime": 12,
        "validator-rpc": 4,
        "routes": 19
      },
      "evidence": {
        "runtime_role": "standby",
        "validator_enabled": false,
        "validator_process_running": false,
        "public_rpc_route_present": false,
        "public_hub_route_present": false
      },
      "evidence_hash": "sha256:..."
    }
  ]
}
```

The verifier definition is part of the assertion contract. For example,
`runtime.is-safe-standby` is true only if every required leaf condition is
observed:

```text
runtime exists
AND configured role is standby
AND validator participation is disabled
AND validator process is not active
AND public RPC routing is absent
AND public Hub routing is absent
```

A false result MUST identify the failed conditions and return the non-secret
evidence needed to diagnose them. Evidence MUST NOT expose private key
material or other secrets; secret-bearing resources are proven through hashes,
ownership, permissions, references, and other safe observations.

Assertions are valid only for the exact resource generations listed in their
result. If any dependency generation changes, the earlier result is stale even
if its recorded boolean was `true`. A journaled assertion result records what
was observed at that time; it is not a reusable source of current truth.

Composite assertions are allowed, for example:

```text
node.is-ready-standby =
    identity.matches-reservation
    AND identity.permissions-secure
    AND runtime.is-safe-standby
    AND routes.public-absent
```

A composite result MUST retain the results and evidence hashes of its leaf
assertions. Mother MUST NOT receive an unexplained top-level `true`.

Every prepared action step declares assertion transitions:

```yaml
requires:
  - identity.matches-reservation
  - routes.public-absent
establishes:
  - runtime.is-safe-standby
retires:
  - runtime.is-absent
preserves:
  - identity.matches-reservation
  - routes.public-absent
```

Mother maintains an active invariant set for the action. An assertion enters
that set only after its establishing step has been verified. It remains active
until a later verified transition explicitly retires or supersedes it. The
temporary interval while a mutation is applying does not advance the action to
the next step and does not retire the old invariant set.

Before every forward step, Mother MUST freshly verify the complete union of:

```text
mandatory control-safety assertions
all currently active action invariants
all invariants the next step declares it will preserve
the next step's direct preconditions
```

This is the default and currently supported guard behavior. Mother does not
check only the immediately preceding step. If an identity, runtime, route, lock,
journal, rollback frame, or other earlier requirement drifted after it was first
established, the next step MUST be blocked.

Mandatory control-safety assertions include at least:

```text
the network/action lock is still owned
the action and journal heads are valid
the active rollback stack agrees with its action and rollback journals
no conflicting provisional action owns an affected scope
the current forward frame is armed-provisional and owned by this action
all promoted rollback frames remain active and journal-consistent
the required schemas and capabilities are supported
```

Before a network-visible mutation, the mandatory set also includes full expected
replica reachability and journal/state agreement.

After a typed mutation returns, Mother freshly verifies the step's complete
postcondition set. It MUST NOT commit
`step-applied-verified-and-promoted`, add the frame to the active rollback-stack
projection, add established assertions to the active set, retire superseded
assertions, or consider the next step before that verification succeeds. A command exit code or mutation response
is not proof of the resulting truth.

Rollback uses the same assertion contract. A rollback frame declares the
assertions that prove its complete prestate has been restored. The frame remains
at the top of the active stack until those assertions are freshly true, the
verification evidence is durably appended to the rollback journal, and the
result is `restored-verified`.

Finalization, pending network-state transitions, reseal, authoritative
checkpoint creation, and finalized-topology transitions MUST freshly verify
their required assertion sets immediately before their journal commit points.

Implementation MUST keep assertion-set selection separate from assertion
execution. Action definitions calculate the complete required set; the guard
registry resolves each assertion name to a versioned verifier; the execution
engine evaluates the selected set and journals the evidence. This separation is
an implementation boundary, not permission to omit active assertions from the
currently supported behavior.



`MOTHER-DESIGN-014: filesystem-journal-atomic-head-and-checkpoint-replay`

`MOTHER-OPEN-010: durable-state-locking-and-atomicity` is resolved by using one
common filesystem journal engine for network, action, and rollback history.
Immutable journal entries and the atomically replaced committed head are
authoritative. Complete state documents, active rollback stacks, current-action
pointers, and summaries are replayable projections.

Every Mother journal has the same physical shape:

```text
<journal-root>/
  metadata.json
  head.json
  entries/
    000000000001.json
    000000000002.json
    ...
  temporary/
  archive/
```

`metadata.json` gives the stable journal identity and kind. A representative
document is:

```json
{
  "schema": "mother.journal.metadata.v1",
  "journal_id": "action:add-node-mainneta-super2-001",
  "journal_kind": "action",
  "state_schema": "mother.action-state.v1",
  "created_at": "..."
}
```

`head.json` identifies the last committed entry:

```json
{
  "schema": "mother.journal.head.v1",
  "journal_id": "action:add-node-mainneta-super2-001",
  "head_sequence": 17,
  "head_entry_hash": "sha256:...",
  "head_state_hash": "sha256:...",
  "committed_at": "..."
}
```

A file in `entries/` is not committed merely because it exists. The exact
commit point is the durable atomic replacement of `head.json` with a head that
names that entry. Entries beyond the committed head are uncommitted orphans and
MUST NOT be interpreted as completed action history.

Every entry records enough information to verify both history and state
transition:

```json
{
  "schema": "mother.journal.entry.v1",
  "journal_id": "action:add-node-mainneta-super2-001",
  "sequence": 17,
  "previous_entry_hash": "sha256:...",
  "previous_state_hash": "sha256:...",
  "event_type": "step-postconditions-verified",
  "event": {},
  "resulting_state_hash": "sha256:...",
  "entry_hash": "sha256:...",
  "created_at": "..."
}
```

The entry hash covers the canonical entry content, including journal identity,
sequence, previous-entry hash, previous-state hash, event type and payload, and
resulting-state hash. Entries are never edited or replaced in place.

### Checkpoint-aware replay

Every checkpoint is an immutable journal entry containing a complete state for
that journal. Checkpoints are not side files and do not bypass the journal head.

A routine checkpoint has the shape:

```json
{
  "schema": "mother.journal.entry.v1",
  "journal_id": "action:add-node-mainneta-super2-001",
  "sequence": 20,
  "previous_entry_hash": "sha256:...",
  "previous_state_hash": "sha256:...",
  "event_type": "state-checkpoint",
  "event": {
    "checkpoint_kind": "routine",
    "covers_through_sequence": 19,
    "covers_through_entry_hash": "sha256:...",
    "state_schema": "mother.action-state.v1",
    "state": {},
    "state_hash": "sha256:..."
  },
  "resulting_state_hash": "sha256:...",
  "entry_hash": "sha256:...",
  "created_at": "..."
}
```

For a routine checkpoint, `event.state_hash`, `resulting_state_hash`, and the
state obtained by valid replay through `covers_through_sequence` MUST be equal.
A routine checkpoint summarizes valid history; it does not override it.

Every newly created journal begins with an initial-state checkpoint before any
ordinary event is committed:

```text
sequence 1: initial-state checkpoint
sequence 2: first ordinary event
sequence 3: second ordinary event
```

The initial checkpoint contains the journal kind's complete defined initial
state. For example, a newly prepared action MAY begin with an action-state
checkpoint containing no completed steps, no active rollback frames, and
`finalized: false`.

When opening a committed journal, Mother MUST:

1. read a stable committed head;
2. begin at the head entry and walk backward by sequence;
3. validate each encountered entry hash, journal identity, sequence, and
   previous-entry relationship;
4. stop at the newest valid checkpoint on that committed lineage;
5. verify the checkpoint's complete state and state hash;
6. reverse the collected later entries into forward order;
7. replay those entries from the checkpoint state;
8. verify every previous-state and resulting-state hash;
9. require the final replayed state hash to equal `head.json`.

Readers MUST NOT assume that replay begins at sequence `1` or that entries
older than the selected checkpoint remain in the active journal directory.
This is the compatibility boundary that permits old history to be archived or
compressed later without redesigning replay.

If Mother opens a journal that has no committed checkpoint, it MUST NOT continue
normal operation as though a checkpoint existed:

- for an empty new journal, it commits the defined initial-state checkpoint;
- for a checkpointless journal with committed entries, it acquires the
  exclusive journal lock, validates and replays the complete retained history
  from the journal kind's defined initial state, and appends a routine
  checkpoint containing the resulting complete state;
- if the initial state is not deterministic, the chain is invalid, or complete
  replay cannot be proven, normal mutation is blocked and explicit
  rectification is required.

The routine checkpoint above is distinct from the authoritative rectification
checkpoint defined by `MOTHER-DESIGN-010`. A routine checkpoint MUST equal
valid prior replay. An authoritative rectification checkpoint is
operator-approved, records the superseded lineage and evidence, and MAY establish
a different active state after unreconcilable history. Both are immutable
checkpoint entries and both become active only through normal head commit.

No automatic checkpoint frequency, retention threshold, archive policy, or
compression command is part of the current contract. The implementation MUST
support appending and discovering checkpoints now; policy for adding later
routine checkpoints MAY be introduced without changing the journal format or
replay algorithm.

### Atomic filesystem commit

A mutating journal writer MUST hold the applicable exclusive operating-system
lock and commit one entry in this order:

```text
1. Read and verify the current committed head.
2. Derive and validate the next complete state.
3. Construct the next immutable entry.
4. Write the entry to a temporary file in the same filesystem.
5. Flush and fsync the temporary entry.
6. Atomically rename it to entries/<sequence>.json.
7. Fsync the entries directory.
8. Write the replacement head to a temporary file.
9. Flush and fsync the replacement head.
10. Atomically replace head.json.
11. Fsync the journal directory.
12. Rebuild or atomically replace derived projections.
```

Step 10 is the commit point. A crash has deterministic meaning:

```text
temporary entry exists:
  incomplete write; never committed

final entry exists but head does not name it:
  orphan entry; never committed

head names a valid entry but a projection is stale:
  transition committed; rebuild the projection by replay

head names a missing or invalid entry:
  journal cannot be proven; block mutation and require recovery
```

A writer MUST NOT update `committed-state.json`, an active rollback stack,
current-operation pointer, or action summary first and attempt to append its
journal evidence afterward.

Derived JSON files use the same local replacement discipline:

```text
write temporary
flush and fsync
atomic replace
fsync containing directory
```

A stale or missing derived file is repairable from replay. An invalid committed
journal head or broken committed hash chain is not silently repaired from a
projection.

### Locking model

The initial implementation permits only one mutating Mother action per network
at a time. It uses an operating-system-backed exclusive lock under:

```text
/runtime/state/mother/locks/networks/<network>.lock
```

The kernel lock is authoritative. JSON metadata written beside or inside the
lock file is diagnostic only and MAY contain the process ID, action ID, owner
identity, acquisition time, and owned scopes. File existence, age, or metadata
alone MUST NOT be treated as proof that a lock is held, and a stale-looking
lock MUST NOT be broken based only on wall-clock time.

The network mutation lock serializes updates to the local network journal and all
action or rollback journals that can change that network. It is necessary but
not sufficient distributed fencing. An established network also uses the
full-set successor-reservation protocol in `MOTHER-DESIGN-026`; no local kernel
lock, operation pointer, process identity, or copied certificate authorizes a
network transition by itself.

Remote Mother replicas, guards, and routing controllers additionally take
operating-system-backed locks for the local resources named by a mutation scope.
A guard MUST refuse a capture, mutation, restore, or journal-affecting request
when an incompatible resource lock is held or when the request lacks the exact
currently valid full-set successor certificate required by
`MOTHER-DESIGN-026`.

`diagnose` remains read-only and does not acquire the mutation lock. A read-only
journal open MUST read the head before and after replay. If the head changed, it
discards the result and retries from the new stable head. Mutating commands
acquire the lock before trusting replay for a write decision and verify lock
ownership as a mandatory guard assertion before every step.

### Cross-journal transitions

Mother MUST NOT pretend that two independent `head.json` replacements are one
atomic transaction. Every durable fact has exactly one owning journal and one
commit point:

```text
network-visible durable transition, pending-action state, and accepted topology:
  owned by the network journal

forward action stage, verified step, remediation decision, and finalize preparation:
  owned by the action journal

rollback frame attempt, closure preparation, and restoration evidence:
  owned by the rollback journal
```

A committed entry MAY reference another journal only by stable identity,
sequence, entry hash, and resulting-state hash. Derived operation state MAY
combine several independently verified journal heads, but no fact becomes true
merely because a projection was updated.

This rule is especially important for finalization. A distributed network action
uses a three-journal protocol:

1. Mother appends `frame-close-prepared` records to the rollback journal for
   every still-active promoted frame.
2. Mother commits `finalization-prepared` to the action journal, referencing the
   exact rollback-journal head, closure records, desired finalized topology, and
   current pending network-journal head.
3. Mother commits `pending-action-finalized` to the network journal, referencing
   that exact action-journal entry and rollback-journal evidence.

The atomic network-journal commit of `pending-action-finalized` is the
irreversible boundary for a network-scoped action. In one replay transition it:

```text
sets finalized_topology to the pending desired topology
advances finalized_topology_epoch
records the action as finalized
clears the active pending_action field
makes the referenced rollback frames permanently non-executable
```

Mother MAY then append an `action-finalized` mirror entry to the action journal
that references the exact network-journal finalization entry and rebuild local
projections. That mirror is required for a complete action history, but it is not
a second finalization authority.

Crash interpretation is deterministic:

```text
closure/finalization-prepared records committed, network finalize not committed:
  action is not finalized
  rollback remains available
  preparation records remain historical evidence

network pending-action-finalized committed, action mirror missing:
  action is finalized
  rollback is closed
  startup appends or reconstructs the missing action-journal mirror
```

Finalization authority and replica convergence MUST be reported as distinct
states:

```text
finalization-not-committed:
  no authoritative network-journal finalization entry exists
  rollback remains available

finalized-replication-pending:
  the authoritative finalization entry exists
  rollback is permanently closed
  one or more expected replicas have not acknowledged that exact head
  all new mutation is blocked

finalized:
  every expected replica has durably acknowledged the exact finalization head
```

The exact full-set acknowledgement and recovery protocol for the middle state
remains part of `MOTHER-OPEN-018`. Every acknowledgement or recovery transition
MUST use the single-successor certificate contract in `MOTHER-DESIGN-026`.

A later rollback chosen before the network finalization commit appends an event
identifying unused `frame-close-prepared` or `finalization-prepared` records as
abandoned by that attempt. History is preserved; old records are never
rewritten.

### Rollback and finalize ordering

The common journal commit point enforces the rollback rule.

After a guard restores a frame's complete prestate and freshly verifies the
required assertions, Mother MUST:

```text
append rollback-restored-verified
commit the rollback-journal head
replay/rebuild rollback-stack.json without that frame
```

If Mother crashes after the rollback-journal head commits but before the stack
projection is replaced, startup replay proves the frame is complete and rebuilds
the stack without it. If the head did not commit, the frame remains active and
the idempotent complete-prestate restore MAY be retried.

Finalization follows the cross-journal protocol above:

```text
append frame-close-prepared for each remaining executable frame
commit the rollback-journal head
append finalization-prepared with exact closure and pending-state references
commit the action-journal head
append pending-action-finalized to the network journal
commit and replicate the network-journal head
append the action-finalized mirror referencing the network entry
rebuild active-stack, current-operation, and network-state projections
release the network mutation lock
```

No frame becomes non-executable merely because a projection was cleared or a
prepared closure record exists. For a network-scoped action, finalization is
proven by the committed `pending-action-finalized` network-journal entry and its
exact cross-journal references. If replication acknowledgements are interrupted
after the local network commit, the action is finalized but normal mutation
remains blocked until every expected replica is brought to that committed head.

### Startup and command preflight

On startup, and before any mutating command continues, Mother MUST:

1. acquire the required operating-system lock;
2. validate journal metadata and committed heads;
3. discover the newest valid checkpoint for each required journal by walking
   backward from its head;
4. replay forward from those checkpoints;
5. verify or rebuild committed-state, action-summary, active-stack, and
   current-operation projections;
6. identify temporary files and uncommitted entries beyond the head;
7. compare local network checkpoint/head/replay facts with every expected
   replica;
8. compare unresolved action and rollback state with the affected guards;
9. reconcile durable successor reservations, cancellation tombstones, accepted
   certificate hashes, and any partial or split acquisition across every expected
   replica;
10. block mutation when a committed head, checkpoint, required lineage, exact
   reservation owner, or successor claim cannot be proven.

Temporary files and orphan entries MAY be archived after diagnosis, but they
MUST NOT be promoted to committed history merely because their contents look
plausible.

Replica agreement for a journal compares at least:

```text
journal ID and state schema
selected checkpoint sequence
selected checkpoint entry hash
selected checkpoint state hash
head sequence
head entry hash
final replayed state hash
```

Equal head sequence numbers alone do not prove agreement.

This design applies equally to the global network journal, each forward action
journal, and each rollback journal. The event/state schemas differ; the
immutable-entry, checkpoint discovery, atomic-head commit, operating-system
locking, and replay rules remain identical.



`MOTHER-DESIGN-015: distributed-add-remove-with-network-wide-rollback`

`MOTHER-OPEN-009: route-reconcile-api-contract` is resolved by making validator
membership, RPC routing, and Hub/FDB topology ordinary typed phases of the same
distributed `add-node` or `remove-node` action. The exact live soft-voting
contract for the validator-membership phase is sealed by `MOTHER-DESIGN-018`.
The operator does not run a separate topology action and does not manually
propagate routes after a node action.

### Full-network clean-state barrier

Before preparing either node action, Mother MUST query the union of the sealed
`replica_hosts` set, every host carrying a current network node, and the proposed
target host. Current replicas and current-node hosts MUST freshly prove the full
state-agreement barrier below. A target host that is not yet enrolled MAY report
itself as `prospective-unenrolled`; it MUST still prove reachability, compatible
capabilities, no pending work, no conflicting locks, and a clean bootstrap scope,
but it is not counted as an agreeing replica until the explicit enrollment
contract in `MOTHER-OPEN-017` completes.

```text
reachable
same complete network checkpoint/head/replayed-state hash
pending_action is absent
no unresolved Mother action
no executable rollback frame
no provisional guard frame
no conflicting local resource lock
no active successor reservation owned by another operation
no unresolved partial or split successor-reservation acquisition
supported action, guard, route, journal, and successor-reservation schemas
```

The action does not begin when any expected host is unavailable or pending.
Majority agreement is insufficient. `prep` performs a read-only barrier
evaluation and freezes the exact participant set, canonical replica-set hash,
head tuple, generations, prepared-intent hash, and assertion evidence.
Immediately before the first mutation, `do` acquires the local network lock,
freshly revalidates that frozen barrier, and obtains the full-set successor
certificate defined by `MOTHER-DESIGN-026`. The first certified successor opens
and fully replicates the pending action. Only then can the first live
infrastructure mutation be dispatched. A successful `prep` never substitutes
for the locked `do` revalidation or the distributed certificate.

### Distributed rollback layers

One logical action MAY contain rollback layers whose participant frames are
stored on several machines. A distributed layer becomes `armed-provisional`
only after every required participant has durably captured the complete prestate
for its local scope and Mother has journaled all participant frame references.
It is not yet part of the completed rollback stack.

A representative provisional layer is:

```yaml
layer_id: rollback-hub-fdb-0007
kind: distributed-complete-prestate
participants:
  - host: coolify-a
    node: mainneta-super1
    frame_id: frame-...
    prestate_hash: sha256:...
  - host: coolify-c
    node: mainnetc-super1
    frame_id: frame-...
    prestate_hash: sha256:...
required_participant_count: 2
status: armed-provisional
```

After the forward distributed mutation, every required participant MUST freshly
verify the intended poststate and generation. Mother MUST NOT commit the
logical layer's `step-applied-verified-and-promoted` transition or add it to the
completed active rollback-stack projection before every required participant
passes that verification.

A promoted distributed layer remains executable until every participant has
restored its prestate, freshly verified the restoration assertions, and
committed `restored-verified` evidence to its rollback journal. One failed or
unreachable participant leaves the layer on top of the action stack and blocks
lower-layer rollback. A failed forward layer remains provisional above the
completed stack and follows the remediation contract in `MOTHER-DESIGN-016`.

### Typed RPC routing resource

RPC routing is reconciled per affected Coolify host as a complete
Mother-owned desired-state resource. The baseline local control surface is:

```text
POST /guard/v1/prestate/capture
PUT  /guard/v1/rpc-routing/state
POST /guard/v1/assertions/verify
```

The desired state names the complete host-local canonical RPC scope:

```json
{
  "schema": "mother.guard.rpc-routing-state.v1",
  "action_id": "add-node-mainnetc-super1-001",
  "step_id": "reconcile-rpc-routing",
  "frame_id": "frame-...",
  "network": "mainnet",
  "host": "coolify-c",
  "expected_generation": 21,
  "ownership": {
    "owner": "mother",
    "route_kind": "canonical-rpc"
  },
  "canonical_hostname": "mainnet-rpc.greatlibrary.io",
  "router": {
    "entrypoints": ["websecure"],
    "tls": true
  },
  "backends": [
    {
      "node": "mainnetc-super1",
      "url": "http://mainnetc-super1:8545"
    }
  ],
  "expected_chain_id": "<expected-chain-id>"
}
```

The listed backends replace the complete Mother-owned backend set for that host
and route kind. The controller MUST preserve unrelated Coolify, operator, and
application routes. Before mutation it captures the complete owned router,
service, backend, middleware, TLS, filename, permission, and generation state.

Required RPC assertions include:

```text
rpc-routing.matches-desired-owned-graph
rpc-routing.traefik-loaded-generation
rpc-routing.backends-reachable
rpc-routing.public-probe-returns-expected-chain
rpc-routing.target-membership-matches-action
```

A node in private standby MUST also be provably absent from every Mother-owned
RPC backend set, not merely absent from one route file.

### Typed Hub/FDB topology resource

Hub/FDB topology is reconciled on every current node whose forwarding or peer
view changes. The baseline local control surface is:

```text
POST /guard/v1/prestate/capture
PUT  /guard/v1/hub-fdb/state
POST /guard/v1/assertions/verify
```

The desired state is complete for one node and one topology generation:

```json
{
  "schema": "mother.guard.hub-fdb-state.v1",
  "action_id": "add-node-mainnetc-super1-001",
  "step_id": "reconcile-hub-fdb",
  "frame_id": "frame-...",
  "network": "mainnet",
  "node": "mainneta-super1",
  "expected_generation": 12,
  "desired_topology_epoch": 58,
  "peers": ["mainnetc-super1"],
  "forwarding_entries": [],
  "handoff_targets": [],
  "node_routes": [],
  "desired_state_hash": "sha256:..."
}
```

The exact lists are network-policy outputs, but replacement semantics are fixed:
the payload describes the complete Mother-owned Hub/FDB state for that node and
generation. Before mutation, the guard captures the complete prior peer,
forwarding, handoff, route, configuration, and generation state.

Required Hub/FDB assertions include:

```text
hub-fdb.matches-desired-topology
hub-fdb.peer-reachability-valid
hub-fdb.forwarding-entries-valid
hub-fdb.handoff-targets-valid
hub-fdb.topology-epoch-matches
```

The distributed Hub/FDB phase succeeds only when every required node reports the
same intended topology epoch and passes its local evidence-backed assertions.

### Integrated `add-node` sequence

`add-node` is one user action:

```text
prepare service and identity
-> admit validator
-> reconcile RPC routing
-> reconcile Hub/FDB topology everywhere
-> verify complete network
-> ready-to-finalize
```

Each phase below follows the same rule: capture and arm its provisional frame
or distributed layer, perform the mutation, freshly verify the complete
poststate on every required participant, then durably promote that layer before
the next phase MAY begin.

The ordered mutation sequence is:

1. Acquire the network lock and freshly revalidate the full-network clean-state barrier frozen by `prep`.
2. Create the action journal, rollback journal, participant manifest, and initial
   checkpoint.
3. On the target host, capture and arm service, identity, and runtime prestates.
4. Create/repair the service, install the reserved identity, and establish a
   healthy private candidate.
5. Capture and arm the validator-membership rollback layer for the frozen voter
   and observer manifest, including the before-set hash and baseline block.
6. In soft mode, dispatch the shared guard-mediated proposal to every existing
   validator guard; in initial or hard mode, execute only the prepared mode.
   Verify the same desired effective validator set and post-change block progress
   across every required observer before promoting the layer.
7. Capture and arm complete RPC-route prestates on every affected Coolify host.
8. Reconcile host-local canonical RPC backend sets and verify the public chain
   identity and local backend membership.
9. Capture and arm complete Hub/FDB prestates on every current node, including
   the newly admitted node.
10. Reconcile the complete Hub/FDB topology and verify it on every participant.
11. Confirm that every pending-action transition committed during the sequence
    has been replicated to every expected host, then run the complete active
    guard set.
12. Commit `pending-action-ready-to-finalize` and present
    `do-complete-pending-finalize` to the operator.

The completed add stack is logically:

```text
top
  distributed Hub/FDB topology layer
  distributed/per-host RPC routing layer
  distributed validator-membership layer
  target runtime layer
  target identity layer
  target service layer
bottom
```

Rollback therefore withdraws the new Hub/FDB view, restores prior RPC routing,
restores prior validator membership, and only then dismantles the target runtime,
identity, and service.

### Integrated `remove-node` sequence

`remove-node` is also one user action. Its safe forward order removes traffic and
network dependencies before removing the service:

```text
reconcile Hub/FDB away from target
-> reconcile RPC away from target
-> remove validator membership
-> detach/remove service
-> verify complete network
-> ready-to-finalize
```

Each phase below is promoted only after its complete distributed poststate is
freshly verified. A failed phase remains provisional and blocks later phases.

The ordered mutation sequence is:

1. Acquire the network lock and freshly revalidate the full-network clean-state barrier frozen by `prep`.
2. Verify surviving Hub, RPC, and validator participants are healthy.
3. Capture and arm the Hub/FDB prestate on every current node.
4. Reconcile Hub/FDB topology without the target and verify every survivor.
5. Capture and arm RPC-route prestates on every affected Coolify host.
6. Reconcile all RPC backend sets without the target and verify surviving public
   service.
7. Capture and arm validator-membership prestates for the frozen voter and
   observer manifest while the target validator and guard remain running.
8. In soft mode, dispatch the shared guard-mediated removal proposal, including
   the target's own vote when required; in hard mode, execute only the prepared
   topology operation. Verify target absence, complete set agreement, and
   post-change block progress before promoting the layer.
9. Capture and arm the target service/runtime/identity prestate.
10. Detach, disable, archive, or remove the target exactly as prepared.
11. Confirm that every pending-action transition committed during the sequence
    has been replicated to every expected host, then run the complete active
    guard set.
12. Commit `pending-action-ready-to-finalize` and present
    `do-complete-pending-finalize` to the operator.

The completed removal stack is logically:

```text
top
  target service/runtime layer
  distributed validator-membership layer
  per-host RPC routing layer
  distributed Hub/FDB topology layer
bottom
```

Rollback restores the target service first, restores validator membership and
network health, then restores RPC routing and finally the prior Hub/FDB topology.
The node is not exposed again before the runtime and validator state it depends
on have been restored.

### Commit and finalize boundary

Successful validator voting, RPC routing, or Hub/FDB reconciliation never closes
its rollback layer. All layers remain active after the network appears healthy.
`finalize` freshly verifies:

```text
all expected hosts reachable and clean for this action
validator set and chain progress match the prepared result
every RPC host matches its desired owned route graph
every node matches the desired Hub/FDB topology epoch
all participant rollback frames are present and consistent
all replicas agree on the pending resulting state
```

Only the committed network-journal `pending-action-finalized` entry closes the
complete distributed rollback stack. Network-visible phase events are appended
and replicated as pending-action transitions when they occur; finalization
promotes the pending desired topology to finalized topology and closes the
pending action according to the cross-journal protocol. A command after
finalization is a new action with new prestates; it cannot reopen the old
rollback layers.

`rpc-propagate` MAY remain as an explicit repair/reconciliation command, but
normal add/remove correctness MUST NOT depend on the operator remembering to
run it.


`MOTHER-DESIGN-016: provisional-frame-promotion-and-remediation`

`MOTHER-OPEN-006: provisional-local-action-state-lifecycle` is resolved by
separating an armed provisional frame from a successfully promoted rollback-stack
item.

Every forward mutation layer has this lifecycle:

```text
planned
-> armed-provisional
-> applying
-> applied-awaiting-verification
-> applied-verified-and-promoted
```

A frame or distributed layer is captured and durably armed before mutation so
the recorded prestate is always available. It is promoted onto the active
rollback stack only after the failure condition is gone, the complete desired
poststate is freshly verified, and the action journal commits
`step-applied-verified-and-promoted`. The next forward layer MUST NOT begin before
that commit.

When a step fails, is interrupted, or cannot be fully verified, the action enters:

```text
remediation-required
```

The unresolved provisional layer remains logically above the completed active
stack. It is inspectable but is not reported as a completed or pop-able stack
item. Mother MUST display:

```text
failed or unresolved step
armed provisional frame or distributed participant frames
observed versus expected generations and assertion evidence
completed active rollback layers in strict LIFO order
which contiguous top layers are currently pop-able
why any layer or participant is blocked
the exact remediation commands currently allowed
```

The operator is offered three remediation paths:

```text
1. rollback the entire action
2. rollback one or more contiguous completed layers from the top
3. retry and resume the failed forward step
```

Arbitrary rollback of a middle layer is forbidden. A distributed layer counts as
one logical item even when it contains frames on many hosts.

Before either full or partial rollback can pop a completed layer, Mother MUST
first resolve the unpromoted failed layer. It restores that layer's complete
prestate, freshly verifies the restoration on every required participant,
commits `provisional-restored-verified`, and closes the provisional frame without
promoting it. It then processes the requested number of completed top layers.
A partial rollback leaves the action open in `remediation-required` with a
recomputed active invariant set and a newly reported safe resume point.

Retry/resume reuses the existing armed provisional frame. Mother MUST NOT capture
a new prestate over a partial result. It freshly checks all mandatory safety
assertions and every still-active invariant, then:

```text
desired poststate already holds:
  verify it and promote the existing frame

recognized retryable partial state:
  retry the prepared typed mutation using the existing frame
  verify and promote on success

recorded prestate already holds:
  retry the prepared mutation from that prestate

neither prestate nor desired/recognized partial state can be proven:
  refuse retry and require rollback or explicit rectification
```

For a distributed step, promotion requires every required participant to report
the same action ID, expected generation, durable armed frame, and freshly
verified desired poststate. A missing or unreachable participant blocks
promotion.

The command surface MAY expose these choices as:

```text
mother <kind> do <network>                         # retry/resume
mother rollback <network> --all
mother rollback <network> --count <n>
mother rollback <network> --through <layer-id>
```

`--count` and `--through` operate only on a contiguous prefix from the current
top of the completed stack. The unresolved provisional layer is restored first
when present. The interactive report MAY offer numbered choices, but those
choices map to the same journaled operations.

A provisional frame closes only through one of these durable outcomes:

```text
applied-verified-and-promoted
provisional-restored-verified
closed-by-authoritative-rectification
```

Temporary working copies MAY be cleaned only after the closing event commits.
Finalization is forbidden while any provisional frame remains unresolved.
Conflicting mutations on the network or overlapping scopes remain blocked until
the action is finalized, fully rolled back, or explicitly rectified.



`MOTHER-DESIGN-017: replicated-pending-network-state-until-finalize`

The pending-versus-finalized network-state question is resolved by storing both
in the replicated network journal and complete network-state document.

A network MUST NOT have more than one network-scoped pending action because the
network mutation lock serializes distributed mutation. The replayed network state
has this conceptual shape:

```yaml
network_key: mainnet
finalized_topology_epoch: 42
finalized_topology: {}
pending_action:
  action_id: add-node-mainneta-super4-001
  kind: add-node
  status: applying
  desired_topology_epoch: 43
  desired_topology: {}
  participant_manifest:
    hosts: []
    nodes: []
  phase:
    name: rpc-routing
    status: applied-verified-and-promoted
  applied_network_facts:
    validator_membership: verified
    rpc_routing: verified
    hub_fdb_topology: pending
  provisional_layer_refs: []
  promoted_rollback_layer_refs: []
  participant_receipt_refs: []
  action_journal_ref: {}
  rollback_journal_ref: {}
  remediation_status: null
  rollback_available: true
replica_hosts: []
```

`finalized_topology` is the last topology accepted by `finalize`.
`pending_action` is the complete durable description of the reversible
distributed work currently changing or already reflected in live network facts.
The complete state hash covers both.

The network journal commits a pending-action transition whenever the distributed
action state meaningfully changes. Required transition classes include:

```text
pending-action-opened
participant-manifest-accepted
distributed-layer-armed
distributed-layer-applied-verified-and-promoted
remediation-required
provisional-layer-restored-verified
rollback-layer-restored-verified
pending-action-ready-to-finalize
pending-action-finalized
pending-action-rolled-back
pending-action-rectified
```

Detailed frame payloads, raw probe output, and local retry attempts remain in
their owning action, rollback, or participant journals. The network transition
references those records by stable journal ID, sequence, entry hash, and
resulting-state hash and records enough summary state for every replica to know:

```text
what topology is finalized
what distributed action is pending
what live network phases have been verified
what remains provisional
what rollback layers remain executable
whether remediation is required
which exact participant receipts support the state
```

A checkpoint of the network journal always contains both finalized topology and
the complete pending-action state. Recovery from a checkpoint therefore cannot
forget an unfinished distributed action.

Opening a network-scoped action is itself replicated. After the full-network
clean-state barrier succeeds, Mother commits `pending-action-opened` before the
first network-scoped mutation. Every expected replica MUST acknowledge the new
complete state before the action proceeds to the next distributed phase.

A successful forward phase does not modify finalized topology. It advances the
pending action and records the verified physical facts. A failed phase records
`remediation-required`; retry, partial rollback, full rollback, or rectification
then advances the same pending action rather than creating an unrelated lineage.

Full rollback commits `pending-action-rolled-back`. That transition verifies the
original finalized topology is again true, preserves the complete action history,
and clears `pending_action` without advancing the finalized topology epoch.

Finalization commits `pending-action-finalized` through the cross-journal
protocol in `MOTHER-DESIGN-014`. That single network-journal transition:

```text
copies pending_action.desired_topology to finalized_topology
advances finalized_topology_epoch
records the finalized action and evidence references
clears pending_action
closes the referenced rollback rights
```

The term `committed` MUST therefore be interpreted precisely:

```text
committed journal transition:
  durably part of the active journal lineage

finalized topology:
  operator-accepted topology produced by pending-action-finalized
```

A pending validator-set, RPC, or Hub/FDB change MAY be committed to the journal
and replicated while still reversible. It MUST NOT be presented as finalized
until the network finalization transition commits.

Replica agreement compares the complete replayed state, including the pending
action identity, phase, participant manifest, action/rollback journal references,
remediation status, finalized topology epoch, and complete state hash. A host
that reports only the same finalized topology but a different or missing pending
action does not agree and blocks normal mutation.


`MOTHER-DESIGN-018: guard-mediated-qbft-membership-as-distributed-reversible-layer`

The live validator-membership contract is sealed by adopting the existing
guard-mediated QBFT vote behavior as an ordinary distributed reversible layer
inside `add-node` and `remove-node`.

`tools/allfather_control.py` and `tools/coolify_qbft_network.py` remain reference
implementations for the current RPC behavior. Mother owns the durable action,
participant, assertion, remediation, and rollback contracts. The legacy
`POST /qbft/propose-validator` behavior MAY be adapted behind the typed Mother
guard endpoint, but it is not itself the complete Mother contract.

This design applies to `soft` mode. `initial` mode has no prior validators and
therefore no live vote. `hard` mode uses the captured QBFT configuration/topology
contract rather than disguising an offline replacement as a vote.

### Frozen proposal and participant manifest

`prep` creates one logical proposal for the membership layer and freezes:

```text
proposal ID
target validator address
desired membership: present or absent
complete before validator set and hash
complete desired validator set and hash
baseline block number
required voter guards
required observer guards
participant validator addresses
participant guard endpoints and capabilities
rollback membership target
```

For a soft add:

```text
voters:
  every validator in the finalized before-set

observers:
  every before-set validator guard
  plus the private candidate guard

candidate:
  not a voter for its own admission
```

For a soft remove:

```text
voters:
  every validator in the finalized before-set, including the target

observers:
  every before-set guard, including the target guard while its runtime remains up

target:
  remains running, reachable, and able to report the resulting set until the
  membership layer is promoted or restored
```

Mother does not silently shrink either set after `prep`. QBFT can enact the
change before every requested vote is submitted, but every required voter MUST
remain reachable and produce a durable vote receipt, and every required observer
MUST produce durable observation evidence for the same effective validator set
before promotion.

Each validator maps to exactly one approved local guard endpoint reached through
the Coolify-authorized private call path. Public RPC routes and RPC-only
non-validator services are never automatic voting participants.

### Typed guard vote endpoint

The baseline soft-vote surface is:

```text
POST /guard/v1/prestate/capture
POST /guard/v1/qbft/validator-membership/vote
GET  /guard/v1/qbft/validator-membership/receipts/<receipt-id>
POST /guard/v1/assertions/verify
```

Each participant first captures and arms its local membership-observation scope
under the distributed provisional layer. A voter frame additionally arms the
local vote-attempt scope. The frame records at least:

```text
local validator identity
effective validator set and canonical set hash
target validator address
desired presence
baseline block number
validator-RPC endpoint identity
resource generation
proposal ID
```

The vote request is one participant attempt under the frozen proposal:

```json
{
  "schema": "mother.guard.qbft-validator-membership-vote.v1",
  "action_id": "add-node-mainneta-super4-001",
  "step_id": "admit-validator",
  "layer_id": "validator-membership-0004",
  "proposal_id": "qbft-membership-0004",
  "attempt_id": "coolify-a-validator-1-attempt-1",
  "idempotency_key": "idem-...",
  "participant_frame_id": "frame-...",
  "network": "mainnet",
  "local_validator_address": "0x...",
  "target_validator_address": "0x...",
  "desired_present": true,
  "expected_before_set_hash": "sha256:...",
  "desired_set_hash": "sha256:...",
  "baseline_block_number": 12345
}
```

For a frozen voter, the guard MUST:

1. prove that its local validator RPC is running and maps to the frozen voting
   validator;
2. freshly read the effective validator set;
3. return `already-desired` without submitting a vote when the desired
   membership is already true;
4. otherwise call `qbft_proposeValidatorVote(target, desired_present)`;
5. freshly read the effective set and block number again;
6. append a durable vote receipt before returning.

For every frozen observer, including a non-voting add candidate, the
membership assertions append durable observation evidence containing the
effective set, set hash, block number, verifier version, and resource
generation.

A durable vote receipt contains at least:

```json
{
  "schema": "mother.guard.qbft-validator-membership-receipt.v1",
  "receipt_id": "receipt-...",
  "receipt_sequence": 18,
  "receipt_hash": "sha256:...",
  "action_id": "add-node-mainneta-super4-001",
  "proposal_id": "qbft-membership-0004",
  "attempt_id": "coolify-a-validator-1-attempt-1",
  "participant_frame_id": "frame-...",
  "local_validator_address": "0x...",
  "target_validator_address": "0x...",
  "desired_present": true,
  "before_validator_set": [],
  "before_set_hash": "sha256:...",
  "vote_result": true,
  "after_validator_set": [],
  "after_set_hash": "sha256:...",
  "baseline_block_number": 12345,
  "observed_block_number": 12346,
  "status": "vote-submitted"
}
```

Allowed receipt statuses include:

```text
already-desired
vote-submitted
desired-observed
vote-rejected
validator-rpc-not-running
participant-mismatch
membership-drift
vote-failed
```

A compatibility adapter MAY map the current guard statuses
`already-admitted`/`already-removed` to `already-desired` and
`admitted`/`removed` to `desired-observed`.

`vote-submitted` is intermediate evidence only. It never proves membership
success.

Repeating the same `attempt_id` and identical request hash returns the same
durable submission receipt. A retry after a failed or inconclusive attempt uses
a new attempt ID under the same proposal and the same armed provisional frame.
Before submitting another vote, the guard MUST first check whether the desired
effective set already exists.

### Distributed success and promotion

The membership layer remains provisional until Mother freshly proves:

```text
every required participant is reachable
every required voter has a durable vote receipt referenced by hash
every required observer has durable observation evidence referenced by hash
every observer reports the same effective validator-set hash
the effective set equals the prepared desired set
the target's presence or absence matches the action
chain block production advanced beyond the captured baseline after the change
no participant reports a foreign proposal or unexpected membership generation
```

The standard assertions are:

```text
qbft.required-participants-accounted-for
qbft.validator-set-matches-desired
qbft.target-membership-matches
qbft.participant-set-agrees
qbft.post-membership-block-progresses
```

Mother then commits one
`step-applied-verified-and-promoted` transition referencing every voter receipt
and observer evidence record. The replicated pending network state records the
proposal ID, before and desired set hashes, participant evidence references,
observed block evidence, and promoted rollback-layer reference.

A successful HTTP response, `vote_result: true`, or `vote-submitted` receipt is
not a promotion condition.

### Retry, resume, and remediation

When voting or observation does not converge, the layer remains
`armed-provisional` and the action enters `remediation-required`.

`retry`:

```text
keeps the existing proposal and provisional prestate frames
freshly checks effective membership first
creates a new participant attempt only where the desired set is not yet true
journals every new receipt
```

`resume`:

```text
does not submit a vote when the prepared desired set is already proven
collects missing observations and receipts
promotes only after the complete distributed success condition holds
```

A changed target, changed desired set, changed participant manifest, or
unexpected validator-set hash is not a retry. It requires rollback,
rectification, or a new prepared action.

### Rollback is a compensating membership transition

Validator membership is consensus state, so its complete prestate is restored
through a compensating distributed membership transition rather than by writing
one participant's local files.

Rollback of a successful add occurs only after later Hub/FDB and RPC layers have
been restored. The candidate remains running while the current validator set,
including the added validator where required, votes the target back out. The
membership layer is popped only after every required observer reports the
captured before-set and post-transition block progress is proven.

Rollback of a successful remove occurs after the target service/runtime layer
has been restored and the target is reachable as a private candidate. The
surviving validators vote the target back in. The layer is popped only after the
captured before-set and later chain progress are freshly proven everywhere.

A failed unpromoted membership attempt uses the same compensation rules to
restore the captured before-set, then closes the provisional layer without ever
placing it on the completed rollback stack.

For hard mode, rollback restores the captured complete QBFT configuration and
topology through the hard-mode restore contract. For initial mode, rollback
restores the captured zero-node/bootstrap prestate. Mother MUST NOT switch among
initial, soft, and hard recovery mechanisms after `prep`.

### Authority and receipt chaining

Remote guards own durable local vote receipts and membership-observation
evidence. The active local Mother owns the authoritative action and rollback
journals and references each record by participant, sequence, hash, role, and
resulting observed-set hash. The replicated network journal references the
resulting distributed phase evidence.

A membership layer cannot be promoted, restored, or popped based only on
unreferenced response bodies or the call-runner's exit status.

`MOTHER-DESIGN-019: coolify-api-key-only-local-control-boundary`

Mother does not introduce an independent authentication or authorization system
for local Mother and guard APIs. The security boundary for remote control is the
Coolify API credential already required to create, start, inspect, or replace
the private call-runner on a Coolify host.

The baseline contract is:

```text
operator-side Coolify client:
  authenticates with the Coolify API credential
  creates or invokes the private call-runner

private call-runner:
  performs the approved structured local call
  supplies action, request, scope, and idempotency identifiers
  does not supply a second bearer token, client certificate, or request signature

Mother/guard local API:
  accepts calls only on localhost or the private host/container network
  relies on Coolify-mediated placement as the authorization decision
  applies all normal journal, lock, prestate, assertion, and rollback safety rules
```

Local access is explicitly outside the defended boundary. A process that can
access the host, private container network, Docker/Coolify runtime, mounted
state, or arbitrary local service execution is assumed capable of taking full
control. Mother does not attempt to defend against that actor with local bearer
tokens, mutual TLS, per-endpoint credentials, request signing, or role-based
authorization.

The distinction between authorization and operation safety remains important.
The absence of a second local credential does not bypass:

```text
network and resource locks
prepared action identity
request and idempotency identifiers
complete prestate capture
guard assertions
participant manifests
journal commit rules
rollback and finalization boundaries
```

Those controls prevent accidental, stale, conflicting, or unrecoverable
operations; they are not intended to establish a second security perimeter.

The Coolify API credential itself MUST NOT be placed in durable Mother state,
network journals, action journals, rollback journals, checkpoints, guard
receipts, or replicated private state. Existing operator-side Coolify secret and
environment-loading conventions remain the source for that credential.

If Mother or guard endpoints are ever exposed beyond the trusted local/private
host boundary, this threat model MUST be reopened before that exposure is
allowed. Under the current local-only architecture,
`MOTHER-OPEN-013: local-control-api-authorization` is resolved.

`MOTHER-DESIGN-020: governance-office-truth-through-standard-guard-assertions`

`MOTHER-OPEN-014: governance-office-deployment-invariant` is resolved through
the standard evidence-backed assertion path defined by `MOTHER-DESIGN-013`.
Governance identity does not introduce a second truth or verification system.

Mother MUST keep the expected and observed facts distinct:

```text
expected governance offices:
  addresses derived from /runtime/state/mother/identity.private.yaml

observed governance offices:
  office holders read from the authoritative governance contract bindings
  on the expected network

governance consistency:
  expected O0/Captain, O1, O2, and O3 exactly equal the observed holders
```

The private identity file is authoritative for Mother's intended identities and
signing material. It is not proof of what is currently deployed. The finalized
on-chain contracts are authoritative for the currently deployed office holders,
but they do not authorize Mother to silently replace its intended identities.
The required invariant is exact agreement between the two independently
observed facts.

The baseline typed assertion is:

```text
governance.offices-match-private-identity
```

It is evaluated through the existing endpoint:

```text
POST /guard/v1/assertions/verify
```

The assertion's versioned verifier MUST:

1. load the expected network and governance contract bindings from the finalized
   Mother network state;
2. load the expected Captain/O0, O1, O2, and O3 identities from
   `identity.private.yaml`;
3. derive and verify each recorded address from its private identity material
   without exposing that material;
4. prove that the connected chain ID matches the expected network;
5. prove that the configured contract addresses contain the supported governance
   contract code or capability;
6. read the four observed office holders at a block satisfying the verifier's
   declared finality rule;
7. normalize and compare every expected and observed address;
8. return non-secret evidence for both sides and the comparison result.

A representative result is:

```json
{
  "schema": "mother.guard.assertion-result.v1",
  "name": "governance.offices-match-private-identity",
  "verifier": "mother.guard.governance-offices.v1",
  "result": true,
  "scope": {
    "network": "mainnet"
  },
  "dependencies": {
    "private_identity_hash": "sha256:...",
    "network_state_hash": "sha256:...",
    "governance_binding_hash": "sha256:..."
  },
  "evidence": {
    "chain_id": "<expected-chain-id>",
    "observed_block_number": 123456,
    "observed_block_hash": "0x...",
    "finality_rule": "mother.governance-finality.v1",
    "contracts": {
      "governance": {
        "address": "0x...",
        "code_hash": "0x...",
        "reader": "mother.governance-reader.v1"
      }
    },
    "expected": {
      "O0": "0x...",
      "O1": "0x...",
      "O2": "0x...",
      "O3": "0x..."
    },
    "observed": {
      "O0": "0x...",
      "O1": "0x...",
      "O2": "0x...",
      "O3": "0x..."
    }
  },
  "evidence_hash": "sha256:..."
}
```

The assertion fails closed when any required truth cannot be proven. Structured
failure reasons include at least:

```text
private-state-unreadable
private-address-derivation-mismatch
wrong-chain
governance-binding-missing
contract-not-deployed
contract-code-or-capability-mismatch
unsupported-contract-reader
office-read-failed
insufficient-finality
office-address-mismatch
```

A false or unavailable result MUST identify which expected and observed facts
were obtainable, but it MUST NOT expose private keys. Mother MUST NOT
automatically rewrite `identity.private.yaml`, rotate keys, redeploy contracts,
or submit governance changes to make the assertion true. A mismatch requires a
separate explicit corrective action with its own prestate, journal, rollback,
verification, and finalization contract.

Action definitions decide when this assertion is active. It is mandatory at
least:

```text
after governance contract deployment and before that action finalizes
before a governance-authorized mutation signs or submits its first transaction
before reseal, rectification, or authoritative checkpoint commits that claim
governance consistency
```

Ordinary node-lifecycle steps do not need to evaluate this assertion unless they
depend on governance authority. Whenever it is required, it MUST be freshly
evaluated immediately before the dependent journal commit. A prior result is
stale when its private-identity dependency, finalized network-state binding, or
relevant on-chain observation changes.

The exact ABI method names and contract adapters are implementation acceptance
criteria for the versioned verifier registry. An unknown contract schema,
unsupported reader, or unverifiable finality rule permits diagnosis but blocks
mutation. This is the same fail-closed compatibility behavior used by every
other guard assertion; it is not a remaining architectural question.


`MOTHER-DESIGN-021: reusable-bounded-call-runner-with-durable-acceptance`

`MOTHER-OPEN-007: call-runner-acceptance-and-result-contract` is resolved by
separating the reusable host transport from durable request and operation state.

Each Coolify host has at most one ordinary Mother call-runner service:

```text
runner service identity:
  stable and host-scoped
  reused across requests
  one active request at a time by default

request identity:
  unique per operator call
  durable independently of the runner
  correlated by request_id, request_hash, and idempotency_key
```

The ordinary service name SHOULD be deterministic, such as
`mother-call-runner`. The control script MUST discover and reuse that service
before creating anything. A second ordinary runner MUST NOT be created merely
because the first runner is stopped, crashed, timed out, or awaiting
reconciliation. A replacement MAY be created only after the existing runner is
explicitly quarantined or removed.

The baseline runner state model is:

```text
idle-or-stopped
  -> starting
  -> executing
  -> idle-or-stopped

starting | executing
  -> crashed
  -> reconciliation-required
  -> idle-or-stopped
```

`crashed` and `reconciliation-required` are retained states, not cleanup
triggers. The service, container history, and Coolify logs remain available
until the associated request has been reconciled. No crash, timeout, lost
response, nonzero exit, or ambiguous result automatically deletes the runner
service.

Every request envelope MUST contain at least:

```json
{
  "request_id": "call-...",
  "idempotency_key": "idem-...",
  "target": "mother",
  "method": "POST",
  "path": "/v1/operations/add-node/prep",
  "body": {},
  "request_hash": "sha256:..."
}
```

`request_hash` covers the normalized target, method, path, and body. Reuse of an
idempotency key with the same request hash returns the existing durable request
or operation record. Reuse of that key with a different request hash fails with
`idempotency-conflict`.

The target Mother or guard API, not the runner, owns acceptance. Before
returning `accepted`, the target MUST durably record:

```text
request_id
idempotency_key
request_hash
target and scope
accepted_at
accepted status
operation_id or participant receipt identity, when applicable
durable status lookup location
```

The baseline durable request states are:

```text
accepted
running
succeeded
failed
remediation-required
rejected
```

A successful transport response SHOULD include:

```json
{
  "request_id": "call-...",
  "request_hash": "sha256:...",
  "status": "accepted",
  "operation_id": "operation-...",
  "status_path": "/v1/requests/call-..."
}
```

The runner's stdout, Coolify logs, container state, and exit code are transport
evidence only. They do not establish whether a request was accepted, completed,
failed, or rolled back.

Crash and retry behavior is deterministic:

```text
no durable request record exists:
  retry the same request_id and idempotency_key

durable accepted or running record exists:
  query status and resume observation
  do not submit a different intent

durable terminal record exists:
  return the existing result

runner state is ambiguous:
  retain the runner and logs
  reconcile the durable request record
  restart and reuse the same service afterward
```

Deleting or replacing the runner MUST NOT delete its durable request record,
operation journal, participant receipts, rollback state, or result. Conversely,
completing a request does not require deleting the runner. The service returns
to an idle or stopped reusable state and handles later requests sequentially.

The operator interface MAY expose explicit runner administration such as:

```text
runner inspect
runner restart
runner reconcile
runner quarantine
runner delete
```

Those commands manage transport only. They MUST NOT silently retry, abandon,
finalize, or roll back a Mother operation.

This resolves the acceptance, result-recovery, crash-retention, and service
reuse questions for the call-runner. Exact Coolify resource identifiers and
wire-field naming are implementation acceptance details, provided the durable
acceptance boundary, idempotency behavior, one-runner-per-host limit, and
no-automatic-cleanup-on-ambiguity rules are preserved.



`MOTHER-DESIGN-022: complete-private-state-on-every-declared-replica`

`MOTHER-OPEN-012: replicated-private-state-policy` is resolved by making every
host explicitly listed in `replica_hosts` a complete private recovery replica.

For the first implementation, every declared replica stores an exact durable
copy of:

```text
/runtime/state/mother/identity.private.yaml
/runtime/state/mother/identity.private.meta.json
/runtime/state/mother/private-recovery/manifest.json
```

and every private recovery object named by the manifest. The recovery set
includes any private prestate snapshot or private artifact required to recover a
currently pending reversible action. It does not include the operator-side
Coolify API credential.

The first implementation replicates the complete private identity document. It
does not create per-network, per-host, or public-only redacted copies. A host
listed as a replica is intentionally trusted with all private identity and
recovery material needed to reconstruct and operate Mother.

The metadata record is non-secret and includes at least:

```json
{
  "kind": "main_computer.mother.private_state_metadata.v1",
  "private_state_kind": "main_computer.mother.private_state.v1",
  "generation": 12,
  "content_hash": "sha256:...",
  "previous_content_hash": "sha256:...",
  "recovery_manifest_hash": "sha256:...",
  "updated_at": "...",
  "updated_by_action_id": "operation-..."
}
```

`content_hash` covers the exact durable bytes of `identity.private.yaml`.
The private-recovery manifest identifies every additional private object by
stable path or object identity, generation, and content hash. The manifest and
ordinary journals MAY contain hashes and private-state references, but they MUST
NOT copy raw private keys or secret payloads.

In addition, every declared replica maintains a non-secret
`recovery-closure/manifest.json` whose hash is sealed in the replicated network
state. That manifest enumerates every immutable object transitively reachable
from the active network, action, participant, rollback, request/result, and
private-recovery journal heads. Reachability includes objects referenced through
other manifests or receipts; a replica is incomplete when it has the journal
reference but not the referenced payload.

Private-state replication uses the same durable participant-receipt model as
other distributed layers:

```text
local head:
  atomically writes and validates the new private state
  records the new generation and hashes
  sends the complete private recovery bundle to every expected replica

each replica:
  writes every private object outside disposable container storage
  validates the private-state schema
  verifies recorded addresses can be derived from the replicated keys
  verifies every manifest object and content hash
  durably records a participant receipt containing only non-secret evidence

local action journal:
  commits references to every required participant receipt

replicated network journal:
  commits the private-state generation, hashes, and distributed receipt set
```

A private-state generation is not considered fully replicated until every host
in the exact expected replica set has returned a durable matching receipt.
Normal mutation, finalization, reseal, and authoritative checkpoint creation are
blocked while any expected replica is unreachable, missing private recovery
material, or reporting a different schema, generation, content hash, or manifest
hash.

A new host MUST receive and validate the complete current private-recovery bundle
before a reseal or replica-set transition MAY make it a required replica. A host
removed from `replica_hosts` stops receiving later generations but cannot be
assumed to have erased material that was previously replicated to it. If a host
is excluded because its trust is revoked or compromise is suspected, removal
from the replica set is not sufficient remediation; the affected identities
MUST be rotated through a separate explicit action.

Private-state disagreement has no automatic winner. Mother MUST NOT silently
copy a local file over agreeing replicas, select a remote copy merely because it
is newer, or reconstruct private keys from public journals. Diagnosis MAY report
all generations, hashes, manifests, and receipt evidence, but adoption of a
different private-state lineage requires explicit recovery or rectification.
The replacement-local-head procedure is defined by
`MOTHER-DESIGN-024`.

Under the trusted-host threat model in `MOTHER-DESIGN-019`, Mother does not add a
second application-level encryption or local authorization layer around replica
copies. Private files MUST still stay on private durable host storage, MUST NOT
be exposed through public routes, logs, reports, ordinary journal entries, or
command output, and MUST be transferred only through the Coolify-authorized
private execution path.


`MOTHER-DESIGN-023: fail-closed-schema-and-capability-negotiation`

`MOTHER-OPEN-011: state-schema-and-capability-negotiation` is resolved by
requiring every state reader, journal replayer, recovery path, and mutating
participant to prove that it understands the exact schemas and capabilities
required by the operation before it MAY alter authoritative state.

Every versioned object MUST identify its schema explicitly. This includes at
least:

```text
network committed state and pending distributed state
network, action, participant, and rollback journal entries
journal checkpoints and journal heads
private-state metadata and recovery manifests
prepared operations and rollback frames
guard requests, receipts, assertions, and restore results
RPC-routing and Hub/FDB desired-state resources
QBFT membership proposals, votes, and observation evidence
head-authority and replacement-head recovery records
```

Every Mother process, guard, route/FDB controller, call-runner target API, and
replica exposes a non-secret compatibility report containing:

```json
{
  "schema": "mother.compatibility-report.v1",
  "component": "mother-guard",
  "component_version": "1.0.0",
  "readable_schemas": [
    "mother.network-state.v1",
    "mother.rollback-frame.v1"
  ],
  "writable_schemas": [
    "mother.guard.participant-receipt.v1"
  ],
  "capabilities": {
    "mother.guard.prestate.capture.v1": true,
    "mother.guard.prestate.restore.v1": true,
    "mother.guard.assertions.verify.v1": true,
    "mother.guard.qbft-membership.vote.v1": true
  }
}
```

The exact field names MAY change, but the compatibility report MUST distinguish
read support, write support, and executable capabilities. Merely reporting a
component version is not proof that an operation is compatible.

`prep` freezes the complete compatibility requirements for the planned action:

```text
schemas that MUST be read
schemas that will be written
guard and controller capabilities that will be invoked
assertion verifier versions that MUST be available
rollback and recovery capabilities required if the action later fails
```

Before `do`, retry/resume, rollback, finalize, reseal, authoritative checkpoint
creation, private-state adoption, or replacement-head activation, Mother
collects fresh compatibility reports from every required participant and proves
that each frozen requirement is satisfied.

The fail-closed rule is:

```text
all required schemas and capabilities are explicitly supported:
  inspection and the requested transition are allowed

a required schema or capability is unknown, absent, ambiguous, or unsupported:
  read-only inspection and export of raw evidence are allowed where safely possible
  authoritative mutation is refused
  rollback/finalize/reseal/recovery activation is refused until a compatible
  implementation or an explicit migration is supplied
```

A component MUST NOT guess at unknown fields, silently discard fields it does
not understand, downgrade state, reinterpret an older action under a newer
contract, or invoke a nearby capability as a substitute. Optional capabilities
that are not required by the frozen operation do not block that operation.

Mixed component versions are allowed only when every participant explicitly
supports the exact schemas and capabilities used by the operation. Matching
version strings are not required; proven compatibility is required.

Schema migration is an explicit, journaled operation. It MUST:

```text
preserve the original bytes and hashes for audit
declare source and destination schemas
run a deterministic validator
produce a complete migrated checkpoint or state object
replicate and verify the result on the full expected replica set
remain rollback-capable until the documented irreversible commit point
```

Migration MUST NOT occur as an implicit side effect of startup, diagnosis, replay,
or recovery.

A replacement Mother MAY download and inspect replica state even when it cannot
yet activate it, but it MUST NOT become the active head until it proves that it
can read, replay, restore, and write every schema and capability required by the
recovered finalized topology and any pending action.


`MOTHER-DESIGN-024: unanimous-replica-recovery-of-a-replacement-local-head`

`MOTHER-OPEN-015: replacement-local-head-recovery-procedure` is resolved by
treating every declared replica as a complete recovery authority and providing a
staged local recovery command that reconstructs `/runtime/state/mother/` from
one unanimous, compatible replica lineage.

The replica recovery set MUST be sufficient to reconstruct the local Mother
state root and execute every still-legal remediation or rollback without the lost
machine. Each replica therefore stores the complete transitive immutable-object
closure reachable from the active journal heads, not merely the journals and
their references. It includes at least:

```text
finalized topology
complete replicated pending distributed action state
network, action, participant, rollback, and request/result journals
journal heads and latest valid checkpoints
replica_hosts and replica-set transition records
unresolved provisional-frame payloads
promoted rollback-frame payloads
complete public and private prestate objects
participant receipt bodies and their durable retrieval metadata
durable request IDs, idempotency records, and accepted/running/result state
every content-addressed object referenced directly or transitively by a recovered journal
identity.private.yaml
identity.private.meta.json
private-recovery/manifest.json and every named private recovery object
recovery-closure/manifest.json and its sealed manifest hash
schema and capability metadata
current head-authority record
```

The operator supplies the Coolify API credential separately. It is not recovered
from replicas. To locate the replica set, the operator supplies a small
non-secret recovery descriptor or equivalent command arguments containing the
network identity and expected Coolify host references.

The recommended command surface is:

```text
python tools/mother/mother.py recover-head prep mainnet --descriptor mother-recovery-mainnet.json
python tools/mother/mother.py recover-head do mainnet
python tools/mother/mother.py recover-head finalize mainnet --reason "original local head lost"
```

`recover-head prep` is read-only against authoritative state. It:

1. contacts every host listed by the recovery descriptor and discovers the
   sealed expected replica set;
2. requires every expected replica to be reachable;
3. collects journal bases and heads, checkpoint hashes, complete state hashes,
   finalized topology, pending action identity and phase, private-state
   generation and hashes, private-recovery manifest hash, recovery-closure
   manifest hash, compatibility reports, and current head-authority metadata;
4. verifies each retained journal chain and checkpoint relationship and walks
   every reference in the recovery-closure manifest;
5. proves that every referenced immutable object exists, matches its content
   hash, and is retrievable from every declared replica;
6. requires exact full-set agreement on one lineage, one complete recovery state,
   and one transitive recovery-object closure;
7. freezes that candidate, closure root, object inventory, and receipt hashes in
   a local recovery plan.

It MUST NOT select the first host that answers, use majority quorum, prefer the
highest generation automatically, merge divergent lineages, omit a pending
action, or reconstruct private keys from public journals.

`recover-head do` downloads the frozen recovery candidate and its complete
transitive object closure into a staging directory, verifies every object,
reference, and hash before publication, and atomically restores the local durable
state root. In particular, it restores:

```text
/runtime/state/mother/identity.private.yaml
/runtime/state/mother/identity.private.meta.json
/runtime/state/mother/private-recovery/
```

without printing secret contents. It then walks backward from each committed
journal head to the newest valid checkpoint, replays forward, rebuilds every
derived projection, and proves:

```text
replayed finalized topology == restored finalized topology
replayed pending action == restored pending action
replayed rollback state == restored action/rollback projections
private-state bytes and manifest == agreed replica hashes
recovered immutable-object closure == sealed recovery-closure manifest
every still-executable rollback/request reference resolves to verified local bytes
```

Recovery preserves an unfinished distributed action exactly as recovered. It
does not silently retry, finalize, abandon, or roll back that action.

Before activation, Mother queries the live guards and controllers and runs the
full required assertion set for the recovered state. Live evidence can identify
drift or place the pending action into remediation-required, but it MUST NOT
silently rewrite the recovered journal lineage.

Head authority is a replicated operational generation, not a secret credential.
The replicated network state contains at least:

```json
{
  "schema": "mother.head-authority.v1",
  "head_id": "mother-head-...",
  "head_epoch": 7,
  "previous_head_id": "mother-head-...",
  "activated_by_recovery_id": "recovery-...",
  "activated_at": "..."
}
```

`recover-head finalize` is the activation boundary. It:

1. proves the restored local state and every expected replica still agree;
2. proves all required schemas and capabilities are supported;
3. proves the live guard assertions required for activation;
4. creates a new `head_id` and increments `head_epoch`;
5. appends and replicates one `replacement-head-activated` transition;
6. requires durable acknowledgement from every expected replica;
7. only then permits ordinary mutation from the replacement local head.

All later mutating requests carry the current `head_id` and `head_epoch` as
operational ownership metadata. Guards and replicas reject transitions from an
older head epoch. Two copies holding the same current `head_id` and
`head_epoch` are fenced by the full-set exact-successor protocol in
`MOTHER-DESIGN-026`; neither can mutate unless every expected replica currently
records the same operation owner and exact proposed successor. The Coolify API
credential remains the only remote security boundary under the trusted-host
threat model.

If any expected replica is unreachable, reports an incompatible schema, lacks
required private recovery material or any object in the transitive recovery
closure, or disagrees on lineage, checkpoint, pending action, private-state
generation, recovery-closure root, or head authority, normal recovery activation
stops. Mother prints the complete disagreement and requires explicit
recovery-rectification or replica-set reseal. There is no automatic winner.

After activation, the replacement Mother resumes the recovered operating state:

```text
no pending action:
  ordinary prep MAY begin after normal preflight

pending action ready-to-finalize:
  operator MAY finalize or roll back

pending action remediation-required:
  operator MAY inspect, retry/resume, or roll back the allowed stack range
```

Replacement-head recovery is therefore reconstruction and authority transfer,
not a hidden topology change and not automatic action completion.



### Full-set successor reservation and single-successor commit

`MOTHER-DESIGN-026: full-set-successor-reservation-and-single-successor-commit`

`MOTHER-OPEN-016: full-set-writer-fencing-and-single-successor-commit` is
resolved for every established network that already has one committed
authoritative head and one sealed expected replica set. Bootstrap authority and
prospective-replica enrollment remain exclusively in `MOTHER-OPEN-017`.

The protocol deliberately chooses safety over collision liveness. A writer does
not discover, elect, or depend on a first lock host. It attempts the exact same
claim on every host in the replica set frozen by `prep`. Requests MAY be
parallel, sequential, retried, delayed, or delivered in different orders. A
writer is authorized only after it proves the complete set. Therefore two
writers can split reservations and wedge progress, but neither can become an
authorized writer or mutate live infrastructure.

The following invariants are normative:

```text
No live infrastructure mutation occurs without a currently valid full-set
successor certificate.

No authoritative network-journal head replacement occurs without a currently
valid full-set successor certificate for that exact predecessor and exact
successor.

A partial reservation set authorizes nothing.

One expected replica accepts at most one operation owner for an expected head
and at most one exact successor hash for each accepted predecessor.

The reservation owner remains the same operation throughout do, remediation,
rollback, finalization, and finalized-replication-pending.

There is no quorum, automatic winner, dynamic anchor, wall-clock expiry, lock
stealing, or fallback replica set.
```

#### Frozen reservation identity

For an established network, `prep` MUST freeze at least:

```text
network_key
expected_head_id
expected_head_epoch
expected_journal_sequence
expected_journal_hash
expected_state_hash
expected_replica_hosts
expected_replica_set_hash
operation_id
prepared_intent_hash
first_successor_sequence
first_proposed_successor_entry_hash
first_proposed_resulting_state_hash
```

`expected_replica_set_hash` is calculated from a canonical serialization of the
sealed host identities. Canonical serialization is used for equality and
certificate ordering only. It MUST NOT be interpreted as an acquisition order or
as authority for a first host.

The proposed successor entry MUST be fully constructed before its claim is sent.
Its entry hash binds the complete immutable transition, including its predecessor
tuple, operation identity, prepared intent, changes, resulting state hash, and
replica-set hash. A writer MUST NOT reuse one claim for different entry bytes,
different resulting state, a different predecessor, or a different operation.

#### Replica-local atomic reservation rule

Every expected replica stores fencing metadata outside the rollback stack and
outside replaceable container state. Conceptual local paths are:

```text
/runtime/state/mother/networks/<network>/successor-reservations/current.json
/runtime/state/mother/networks/<network>/successor-reservations/history/<head-hash>/<operation-id>.json
```

A replica handles a claim under one operating-system-backed lock that serializes
its network journal and successor-reservation state. A claim request contains:

```text
schema
network_key
replica_host
expected_head_id
expected_head_epoch
expected_journal_sequence
expected_journal_hash
expected_state_hash
expected_replica_set_hash
operation_id
prepared_intent_hash
proposed_successor_sequence
proposed_successor_entry_hash
proposed_resulting_state_hash
```

The replica MUST atomically apply this state machine:

```text
local head or replica-set hash differs:
  reject stale-head or replica-set-mismatch

a cancellation tombstone exists for this operation, intent, and expected head:
  reject reservation-cancelled

no operation owns the expected head:
  durably record this operation owner and exact successor claim
  flush the record and parent directory
  return the durable receipt

the same operation and intent own the expected head and claim the same successor:
  return the identical durable receipt

the same operation or intent claims different successor bytes:
  reject successor-mismatch

another operation owns the expected head:
  reject successor-already-reserved
```

A receipt records at least the complete claim fields, replica identity,
reservation-record hash, receipt ID, and receipt hash. Time fields are diagnostic
only. Receipt authenticity is proven by retrieving and revalidating the exact
durable record from the named replica through the trusted Coolify-mediated local
transport; a copied receipt or certificate is not an independently trusted bearer
credential.

A replica MUST NOT automatically expire, replace, steal, or weaken a reservation.
A restart, process death, call-runner replacement, timeout, or lost response
leaves the durable record authoritative.

#### Full-set certificate and first mutation boundary

The writer attempts the claim against every exact expected replica. It enters
`reserving-successor` while acquisition is in progress. If any replica rejects,
times out, is unreachable, or returns an incompatible receipt, the operation
enters `reservation-incomplete`.

A full-set successor certificate contains:

```text
certificate schema
network and operation identity
prepared_intent_hash
exact expected head tuple
exact expected replica hosts and replica-set hash
exact proposed successor sequence, entry hash, and resulting-state hash
one current durable receipt from every expected replica
canonical receipt-set hash
certificate hash
```

Mother MUST persist the certificate under the action before using it:

```text
/runtime/state/mother/actions/<operation-id>/successor-certificates/<successor-sequence>.json
```

Every receipt MUST be revalidated against its replica's current durable
reservation record. Missing, foreign, canceled, stale, or divergent receipts
invalidate the complete certificate.

The complete certificate for `pending-action-opened` is the first authoritative
action of `do`. Mother MUST commit and replicate that exact certified transition
to every expected replica before dispatching any live infrastructure mutation.
The successor reservation is not an ordinary rollback frame. The first
rollback frame is armed only after the certified pending action is replicated
and immediately before the first live mutating substep.

Once a certified transition is committed and replicated, its certificate becomes
the active writer-ownership proof for the resulting head. Live infrastructure
requests use that accepted certificate to prove who owns the current head.
A proposed journal-head replacement uses a new certificate that binds the exact
current predecessor to the exact proposed successor.

Every guard, route controller, participant Mother endpoint, and journal-replica
write endpoint MUST reject a mutating request that does not name the applicable
proof:

```text
live infrastructure request:
  operation_id
  prepared_intent_hash
  active_writer_certificate_hash
  current head tuple
  expected replica-set hash
  step identity

authoritative journal write:
  operation_id
  prepared_intent_hash
  successor_certificate_hash
  expected predecessor tuple
  proposed successor sequence, entry hash, and resulting-state hash
  expected replica-set hash
  transition identity
```

The receiving host MUST verify that its current durable reservation record still
matches the operation owner, current head, and applicable accepted or proposed
claim. Mother MUST freshly revalidate the complete replica set before each
authoritative journal transition and before dispatching each new mutating
substep. A stale certificate MUST NOT authorize work merely because it was
complete earlier.

#### One exact successor per journal head

The operation-level owner persists across the action, but each new authoritative
network-journal transition requires a new exact successor claim. Before replacing
a head, Mother MUST:

1. replay and verify the current expected head;
2. construct the complete immutable proposed next entry;
3. claim that exact successor on every expected replica under the same operation
   owner and prepared-intent hash;
4. persist and revalidate the full-set certificate;
5. commit the exact certified entry locally;
6. replicate that exact entry and head to every expected replica;
7. verify each replica reports the certified head before attempting another
   transition that depends on it.

After an accepted transition, each replica advances its reservation record to
the new certified head while retaining the same operation owner. A second
writer using the same operation ID but proposing different successor bytes is
therefore rejected exactly like a different operation. Retries with the same
operation, intent, predecessor, and successor are idempotent.

An interrupted replication leaves one certified successor and blocks further
ordinary mutation until that same transition is reconciled. A replica MUST NOT
accept another successor for the predecessor merely because the certified entry
has not reached every host.

#### Partial acquisition, collision, and cancellation

Acquisition order is irrelevant. For example, concurrent writers can produce:

```text
coolify-a: operation X
coolify-b: operation Y
coolify-c: operation X
```

Neither operation has a full-set certificate, so neither can open a pending
action, advance the journal, or mutate live infrastructure. Diagnosis MUST show
the complete per-replica distribution, exact claim hashes, unreachable hosts,
conflicts, and allowed next commands. Mother MUST NOT automatically choose a
winner.

The same operation MAY retry or resume acquisition using exactly the same
operation ID, intent hash, predecessor, and proposed successor. A different
operation MUST NOT take over any partial reservation.

Explicit rollback before a live mutation performs exact reservation
cancellation. Each replica atomically writes a durable cancellation tombstone
binding the operation, intent, expected head, and canceled claim. A delayed or
retried claim matching that tombstone MUST be rejected. Cancellation is
idempotent and has no automatic timeout.

The operation MUST NOT become `rolled-back`, release its scopes, or permit a new
writer until every expected replica confirms the tombstone or an already
committed successor is reconciled. If any replica is unreachable or ambiguous,
the operation enters or remains `rollback-failed`, and the network remains
blocked.

If reconciliation proves that every expected replica granted the same exact
claim, Mother reconstructs the certificate and resumes or rolls back that same
operation; it does not classify the acquisition as safely absent. If any
journal successor or live mutation receipt exists, simple reservation
cancellation is forbidden. The same operation MUST use its ordinary durable
rollback protocol, and its reservation remains held until rollback is fully
verified and the rolled-back network transition is replicated.

A cancellation attempt that is only partially acknowledged does not authorize a
different operation. Any delayed request from the old operation remains part of
that same blocked operation and MUST be diagnosed and reconciled; it cannot
create a competing authorized successor.

#### Reservation lifetime, release, and recovery

The full-set operation reservation exists beneath the action but outside the
ordinary rollback stack. It MUST survive writer and runner crashes and remain
held:

```text
through do
through remediation-required
through every rollback attempt
through the authoritative finalization commit
through finalized-replication-pending
until full rollback or full finalization acknowledgement completes
```

Finalization closes ordinary rollback at the network-journal commit but does not
release the distributed reservation. Release occurs only when:

```text
rolled-back:
  every expected replica has the exact fully rolled-back network head and
  confirms durable reservation release

finalized:
  every expected replica has acknowledged the exact finalization head under
  MOTHER-OPEN-018 and confirms durable reservation release
```

Release is durable and idempotent. Until every expected replica confirms release,
scope ownership remains active and a new operation cannot obtain a full-set
reservation. Released and canceled records remain as tombstoned history; they
MUST NOT be erased or reused for a different operation.

On startup and before any mutating or remediation command, Mother queries every
expected replica and classifies the reservation distribution:

```text
no active reservation:
  ordinary prep/do preflight can continue

one exact full-set owner and claim:
  reconstruct or verify the certificate and resume only that operation

one owner with a partial claim set:
  reservation-incomplete; retry/resume or exact cancellation only

several owners split across replicas:
  collision; no mutation; explicitly cancel or reconcile named operations

journal advanced under a referenced certificate:
  reconcile that exact successor and same operation

receipt, journal, or local durable record disagreement:
  block and require explicit reservation rectification or reseal
```

There is no automatic winner and no wall-clock expiration. A replacement Mother
can resume the same reservation only by presenting the same operation ID,
prepared-intent hash, and exact claimed successor and by revalidating every
expected replica. Different authority, lineage selection, replica-set change, or
bootstrap remains an explicit recovery/reseal or `MOTHER-OPEN-017` operation.

### Remaining open design nodes

`MOTHER-OPEN-017: zero-network-and-prospective-replica-bootstrap`

The document still needs the explicit enrollment transaction for a host that is
not yet a replica and for a network with no current validators. It MUST define:

```text
current_replica_hosts
prospective_replica_hosts
transition_participants
desired_replica_hosts
```

and govern initial state/private-recovery transfer, first-node bootstrap, adding
the first node on a new host, removing the last node from a host, intentional
removal of the final validator, preservation of network identity at zero
validators, rollback, and the exact point at which a prospective host becomes a
required replica.

`MOTHER-OPEN-018: finalization-replication-state-contract`

The minimum state distinction is fixed as
`finalization-not-committed`, `finalized-replication-pending`, and `finalized`.
The remaining contract MUST define the exact full-set acknowledgement,
retry/recovery, and journal-head transition rules for
`finalized-replication-pending`. Every such transition MUST use
`MOTHER-DESIGN-026`, so two current writers cannot certify different finalization
successors for one expected head.

The sealed-state format, crash and ambiguous-step recovery model, typed guard
prestate contract, evidence-backed full-guard assertion contract, durable
filesystem journal and locking model, integrated distributed route/topology
lifecycle, provisional-frame remediation lifecycle, replicated
pending-versus-finalized network-state model, guard-mediated reversible QBFT
membership contract, Coolify-API-key local-control boundary, governance-office
assertion contract, reusable bounded call-runner contract, complete private-state
replication policy, fail-closed schema/capability contract, replacement-head
recovery architecture, and local-generation adoption/head-fenced projection
repair contract are otherwise design-resolved. `MOTHER-DESIGN-024` requires
complete transitive recovery-object closure before activation,
`MOTHER-DESIGN-025` defines local state adoption and projection maintenance, and
`MOTHER-DESIGN-026` defines fail-closed full-set writer fencing and exact
single-successor certification.

Exact wire-field names, ABI adapters, schema validators, capability registries,
and migration implementations remain implementation acceptance work. They MUST
preserve the resolved contracts and MUST NOT invent a different authority,
quorum, or recovery model.


## Namespace

Everything new uses the Mother namespace.

Recommended layout:

```text
tools/mother/
  mother.py
  diagnose.py
  probe_topology.py
  plan.py
  add_node.py
  remove_node.py
  reseal_qbft.py
  restore_service.py
  rollback.py
  reseal_state.py
  recover_head.py
  common/
    coolify.py
    guards.py
    qbft.py
    governance.py
    inventory.py
    topology.py
    routing.py
    hub_fdb.py
    private_state.py
    sealed_state.py
    compatibility.py
    capabilities.py
    recovery.py
    state_sync.py
    operations.py
    journal.py
    atomic_files.py
    checkpoints.py
    locks.py
    successor_reservations.py
    planning.py
    reporting.py
    rollback_stack.py
    rollback_journal.py
```

Recommended command shape:

```text
# Read-only. Reports local/remote sealed-state differences but never refreshes,
# adopts, or rewrites local state.
python tools/mother/mother.py diagnose mainnet

# Reconstruct a lost local Mother state root from unanimous compatible replicas.
python tools/mother/mother.py recover-head prep mainnet --descriptor mother-recovery-mainnet.json
python tools/mother/mother.py recover-head do mainnet
python tools/mother/mother.py recover-head finalize mainnet --reason "original local head lost"

# Explicit recovery when local/remote seals disagree or the network is wedged.
python tools/mother/mother.py reseal-state prep mainnet --from-live --reason "replica mismatch"
python tools/mother/mother.py reseal-state do mainnet
python tools/mother/mother.py reseal-state finalize mainnet

# Continue without an unreachable expected replica only through an explicit reseal.
python tools/mother/mother.py reseal-state prep mainnet --exclude-host coolify-b --reason "host unreachable"
python tools/mother/mother.py reseal-state do mainnet
python tools/mother/mother.py reseal-state finalize mainnet

# A recovered host is refreshed and explicitly re-included; it never self-rejoins.
python tools/mother/mother.py reseal-state prep mainnet --include-host coolify-b --reason "host recovered"
python tools/mother/mother.py reseal-state do mainnet
python tools/mother/mother.py reseal-state finalize mainnet

# Complete distributed node lifecycle.
python tools/mother/mother.py add-node prep mainnet --node mainnetc-super1 --host coolify-c --mode soft
python tools/mother/mother.py add-node do mainnet
python tools/mother/mother.py add-node finalize mainnet

python tools/mother/mother.py remove-node prep mainnet --node mainneta-super1 --mode soft
python tools/mother/mother.py remove-node do mainnet
python tools/mother/mother.py remove-node finalize mainnet

# Remediation and rollback. Mother resolves the active operation from the control surface.
python tools/mother/mother.py add-node do mainnet                  # retry/resume
python tools/mother/mother.py rollback mainnet --all
python tools/mother/mother.py rollback mainnet --count 2
python tools/mother/mother.py rollback mainnet --through distributed-rpc-routing
python tools/mother/mother.py rollback mainnet --all --operation-id <id>

# Hard full-set topology repair for existing services.
python tools/mother/mother.py reseal-qbft prep mainnet --nodes mainneta-super1,mainnetc-super1
python tools/mother/mother.py reseal-qbft do mainnet
python tools/mother/mother.py reseal-qbft finalize mainnet
```

Names MAY change, but the authority contract MUST NOT change: every
authoritative mutating Mother operation is run as `prep`, `do`, and `finalize`;
every prepared operation accepts the generic `rollback` command until its
documented irreversible commit point. `sync-state` uses its active-generation
pointer commit, and `repair-projections` remains the non-authoritative one-shot
maintenance exemption defined by `MOTHER-DESIGN-025`.

`--standby` is not a normal-path flag. Standby is the default state produced by
`add-node`.


## Relationship to Allfather

Mother MAY reuse Allfather as reference material, not as a shared lifecycle
implementation.

Allowed Allfather reference areas:

- private-state loading conventions;
- Coolify host and token discovery;
- Coolify service inventory shapes;
- guard, identity, health, and status endpoint shapes;
- private probe metadata structures;
- network naming conventions such as `mainneta-super1`;
- existing Besu/QBFT RPC call patterns;
- current super-node runtime status names.

Forbidden Allfather inheritance:

- Mother mutating commands MUST NOT call Allfather `add-node`, `remove-node`,
  removal handoff, admission, service rebuild, or compose synchronization helpers;
- Mother reseal paths MUST NOT depend on image tags, compose replacement, service
  deletion, or service recreation;
- Mother commands MUST NOT reuse a helper whose name, error messages, or safety
  model belongs to another lifecycle operation;
- Mother commands MUST NOT hide mutation inside a function named as a verifier,
  probe, plan builder, or readiness checker.

The correct relationship is:

```text
Allfather tells Mother how the old world is wired.
Mother decides how lifecycle operations are allowed to run.
```

## Mother control container

The Mother control container is a replaceable operator API process. It owns
stage transitions while it is running, but it does not own authoritative state in
its container filesystem. It is not a super-node and it is not part of the QBFT
validator set. It MUST mount `/runtime/state/mother/` and reconstruct its control
view from that durable state root plus live discovery after every start.

### Responsibilities

The Mother control container is responsible for:

- mounting and validating `/runtime/state/mother/`;
- loading private-state and Coolify host configuration from the durable state
  root;
- discovering Coolify services and guard endpoints;
- querying guard status and QBFT RPC state;
- creating immutable prepared operation records;
- enforcing one active prepared operation per declared scope;
- rejecting conflicting commands until the active operation is finalized or
  rolled back;
- executing `do` only from a prepared operation record;
- calling guard and routing APIs for runtime mutations;
- identifying each mutating substep's complete mutation scope;
- capturing complete prestate and durably arming a provisional rollback frame
  before each mutating substep;
- freshly verifying successful poststate and durably promoting the frame before
  the next substep;
- entering remediation-required instead of promoting a failed or ambiguous step;
- exposing rollback for every non-finalized mutating operation;
- exposing the current action, checkpoints, provisional frames, active rollback
  stack, pop-able range, and immutable rollback journal through the API;
- recording checkpoints during `do`;
- verifying postconditions during `finalize`;
- releasing operation ownership only during `finalize` or `rollback`;
- producing human-readable and JSON reports.

The Mother control container is not responsible for:

- running validator processes itself;
- holding authoritative state only inside the container filesystem;
- holding validator keys outside the mounted private identity backend;
- rebuilding super-node images;
- using compose replacement as the normal mechanism for runtime topology
  mutation;
- deleting a super-node service except inside the explicitly prepared target
  phase of `remove-node`;
- recreating a service except inside `add-node` or an explicit `restore-service`
  operation;
- treating service count as consensus truth.

### Persistent operation ledger

Mother MUST keep a durable operation ledger using the common filesystem journal
engine under the Mother-owned state root. A future storage-backend migration is
allowed only through an explicit migration that preserves journal identity,
checkpoint, hash-chain, atomic-head, locking, and replay semantics. A current
command MUST NOT silently substitute a different authority.

Suggested durable state layout:

```text
/runtime/state/mother/
  identity.private.yaml
  identity.private.meta.json
  private-recovery/
    manifest.json
    objects/
  topology.yaml
  version.json
  active-generations/
    <network>.json
  generations/
    <network>/
      <generation-id>/
  adoptions/
    <network>/
      <operation-id>/
        operation.json
        activation-prepared.json
        reconciliation.json
  locks/
  guards/
  routes/
    <network>/
      <host>.json
  networks/
    <network>/
      committed-state.json
      journal/
        metadata.json
        head.json
        entries/
      successor-reservations/
        current.json
        history/
          <expected-head-hash>/
            <operation-id>.json
  actions/
    <operation-id>/
      action-journal/
        metadata.json
        head.json
        entries/
      rollback-journal/
        metadata.json
        head.json
        entries/
      provisional/
        <step-id>/
          <frame-id>.json
      rollback-stack.json
      successor-certificates/
        <successor-sequence>.json
      prestate/
        <frame-id>.json
      summary.json
  current/
    <network>.json
    adoptions/
      <network>.json
    scopes/
      local-adoption_<network>.json
  reports/
    <operation-id>/
      prep-report.json
      do-report.json
      finalize-report.json
      rollback-report.json
```

The network, action, and rollback journals all use the same immutable-entry,
checkpoint-aware filesystem engine. Their checkpoint state schemas differ, but
their lock, atomic commit, head, hash-chain, and replay semantics do not.

The operation ledger is the place where Mother remembers what it has been told
will happen. The live infrastructure is not allowed to be reinterpreted as if the
prepared instruction never existed.

#### Current operation pointer

For each owned scope, Mother MUST also maintain a durable current-operation
pointer. The pointer is a replayable projection used for fast lookup and
operator visibility; the authoritative ownership decision comes from the
committed action journal together with the currently held operating-system
lock. If the pointer disagrees with journal replay, Mother MUST rebuild the
pointer or block when the journal itself cannot be proven.

At minimum, Mother SHOULD maintain:

```text
/runtime/state/mother/
  current/
    mainnet.json
    scopes/
      network_mainnet.json
      service_mainneta-super1.json
      service_mainnetc-super1.json
      validator_0xb5....json
```

Each current-operation pointer MUST include:

```json
{
  "operation_id": "reseal-mainnet-001",
  "kind": "reseal-qbft",
  "stage": "do-complete-pending-finalize",
  "scopes": ["network:mainnet", "service:mainneta-super1"],
  "operation_path": "/runtime/state/mother/actions/reseal-mainnet-001/summary.json",
  "allowed_next_commands": ["finalize", "rollback"]
}
```

The normal operator SHOULD NOT need to remember the operation ID in order to
roll back. `mother diagnose` MUST report the current operation ID and the
allowed next commands, but `mother rollback --network mainnet` MUST resolve the
active operation from the current-operation pointer. Supplying an explicit
operation ID is allowed only as a safety cross-check; it MUST NOT let the
operator roll back a different operation than the one currently owning the
scope.

There can be many completed historical operations, but a scope MUST NOT have
more than one current non-finalized operation. A command with a different intent
MUST NOT create a second current operation. It MUST be rejected until the current
operation is finalized or rolled back.

### Control APIs

The control container SHOULD expose a local/operator-only API or CLI surface with
the following conceptual operations:

```text
GET  /v1/status
GET  /v1/version
GET  /v1/state-root
GET  /v1/diagnose/<network>
GET  /v1/networks/<network>/seal
GET  /v1/networks/<network>/replicas
GET  /v1/networks/<network>/successor-reservations
POST /v1/internal/networks/<network>/successor-reservations/claim
POST /v1/internal/networks/<network>/successor-reservations/cancel
GET  /v1/internal/networks/<network>/successor-reservations/<operation-id>
POST /v1/networks/<network>/repair-projections
POST /v1/networks/<network>/sync-state/prep
POST /v1/networks/<network>/sync-state/do
POST /v1/networks/<network>/sync-state/rollback
POST /v1/networks/<network>/sync-state/finalize
POST /v1/networks/<network>/reseal/prep
POST /v1/networks/<network>/reseal/do
POST /v1/networks/<network>/reseal/finalize
GET  /v1/networks/<network>/current-operation
GET  /v1/scopes/<scope>/current-operation
GET  /v1/requests/<request-id>
POST /v1/operations/<kind>/prep
POST /v1/current/<network>/do
POST /v1/current/<network>/finalize
POST /v1/current/<network>/rollback                 # all/count/through request
POST /v1/current/<network>/retry-resume
POST /v1/operations/<operation-id>/do                 # optional cross-check form
POST /v1/operations/<operation-id>/finalize           # optional cross-check form
POST /v1/operations/<operation-id>/rollback           # optional cross-check form
POST /v1/operations/<operation-id>/retry-resume       # optional cross-check form
GET  /v1/operations/<operation-id>
GET  /v1/operations/<operation-id>/checkpoints
GET  /v1/operations/<operation-id>/successor-certificates
GET  /v1/operations/<operation-id>/remediation
GET  /v1/operations/<operation-id>/provisional-frames
GET  /v1/operations/<operation-id>/rollback-stack
GET  /v1/operations/<operation-id>/rollback-stack/<frame-id>
GET  /v1/operations/<operation-id>/rollback-journal
GET  /v1/guards/<node>/topology-state
```

`repair-projections` is non-authoritative, local-only, atomic, idempotent
maintenance and is explicitly exempt from the general `prep`/`do`/`finalize`
operation contract. It MUST refuse to run while the network's local-adoption
scope is owned by `sync-state`, `recover-head`, or reseal work. It MUST:

1. capture the exact authoritative local journal-head tuple, including journal
   identity, sequence, entry hash, state hash, `head_id`, and `head_epoch`;
2. replay exactly that pinned lineage and build one complete immutable projection
   generation under a temporary generation directory;
3. write and verify a projection-generation manifest that names every projection
   file and its content hash;
4. flush every generated file, the manifest, and the generation-directory
   metadata before publication;
5. re-read the authoritative local head immediately before publication;
6. atomically publish the complete projection set by replacing one durable
   projection-generation pointer only when the head tuple is unchanged;
7. flush the pointer and its parent-directory metadata before reporting success.

Individually replacing projection files is forbidden because a crash could expose
a mixed projection set derived from different journal moments. A crash before the
pointer switch leaves the old projection generation active. A crash after the
pointer switch leaves the new complete generation active.

If the head changed, `repair-projections` MUST discard its temporary generation.
It MAY perform only a documented bounded number of complete retries; after that
bound, or when no automatic retry is configured, it MUST return
`projection-head-changed` without publishing output. It MUST NOT retry
indefinitely under continuous journal activity.

`repair-projections` does not create a current authoritative operation, does not
own network rollback rights, and requires no rollback contract because its
unpublished generation is disposable. It MUST NOT adopt remote state, replace
private state, alter a journal entry or head, change head authority, alter
finalized or pending topology, change rollback rights, or publish a projection
derived from an obsolete head.

`sync-state` is a staged local adoption transaction for an already-authoritative
remote generation. It is not a network-topology mutation and it does not use the
ordinary `pending-action-finalized` commit.

The local-adoption scope is exclusive per network. While owned, it MUST conflict
with another `sync-state`, ordinary authoritative mutation, `recover-head`,
reseal work, and `repair-projections`. Read-only diagnosis and inspection remain
available.

The active-generation pointer and every record needed to decide or reconcile
local adoption MUST remain independently addressable outside the swappable
generation tree. The minimum durable layout is:

```text
/runtime/state/mother/
  active-generations/
    <network>.json
  generations/
    <network>/
      <generation-id>/
  adoptions/
    <network>/
      <operation-id>/
        operation.json
        activation-prepared.json
        reconciliation.json
  current/
    adoptions/
      <network>.json
    scopes/
      local-adoption_<network>.json
```

The `activation-prepared` record, authoritative adoption-operation record,
current-operation projection, local-adoption-scope ownership, and reconciliation
evidence MUST NOT be stored only inside a generation selected through the active
pointer. A pointer switch MUST NOT make the evidence needed to determine its own
commit outcome unreachable. Implementations MAY additionally copy this metadata
into a candidate closure, but the independently addressable transaction metadata
under the Mother state root remains mandatory.

`sync-state prep` MUST:

1. acquire the local-adoption scope;
2. pin the exact current local generation pointer and complete local head tuple;
3. require unanimous agreement from every expected replica on one remote
   candidate;
4. freeze that candidate's journal identity, sequence, entry hash, state hash,
   recovery-closure root, private-state generation and hashes, pending action,
   `head_id`, and `head_epoch`;
5. create a durable adoption plan without changing the active local pointer;
6. enter `sync-prepared`.

`sync-state do` MUST enter `sync-staging`, download the frozen candidate and
complete referenced object closure into an immutable staging generation, replay
and verify every journal and checkpoint, verify private state and pending-action
recovery data, and build all derived projections without activating the
candidate. It MUST flush every staged object, manifest, projection generation,
and staging-directory metadata before entering `sync-ready-to-activate`.

`sync-state rollback` MUST enter `sync-rolling-back`, discard the staged
candidate, and leave the active local generation pointer unchanged. It MUST NOT
promote the previously stale local generation as new authority or modify remote
replicas. After durable cleanup it enters `sync-rolled-back` and releases the
local-adoption scope.

`sync-state finalize` MUST re-read and prove that:

- the active local generation pointer still names the exact prestate pinned by
  `prep`;
- every expected replica still agrees on the exact frozen candidate;
- the candidate still has the same journal identity, sequence, hash, recovery
  closure, private-state generation, `head_id`, and `head_epoch`;
- the complete immutable staged generation still replays and verifies exactly;
- every staged object and metadata file is durably persisted;
- the local-adoption scope is still owned by this exact operation.

Before switching the pointer, finalize MUST durably commit an
`activation-prepared` record that identifies the old pointer, candidate pointer,
complete staged-generation manifest hash, and frozen remote head tuple. It then
MUST atomically replace the local active-generation pointer with the staged
candidate and flush the pointer plus its parent-directory metadata. That pointer
switch is the irreversible `sync-state` commit point.

Before the pointer switch, rollback remains available. After the pointer switch,
adoption is committed and rollback is closed even when the finalize request
returns an ambiguous result. Crash recovery MUST determine the outcome from the
durable pointer:

```text
old pointer active:
  activation did not commit
  finalize retry or rollback remains available

candidate pointer active:
  activation committed
  rollback is closed
  load the already-complete generation
  reconcile the operation record to sync-committed
```

Startup reconciliation MUST read the independently addressable adoption
transaction metadata before interpreting the active-generation pointer. It MUST
NOT attempt post-commit materialization. If the candidate pointer is active but
the terminal operation record is missing, startup MUST load and verify the
already-complete generation, append or repair only the missing reconciliation
record, enter `sync-committed`, and release the local-adoption scope.

A stale candidate MUST NOT be activated merely because it was valid during
`prep`; finalize MUST prove that the local prestate and unanimous remote
candidate remain unchanged immediately before the pointer switch.

`sync-state` MUST preserve `head_id`, `head_epoch`, journal sequence, journal
entry hash, and lineage. It MUST NOT select a lineage, create new authority,
enter `finalized-replication-pending`, or write a network transition because the
expected replicas already possess the adopted generation. Any authority change,
lineage selection, replica disagreement, or reseal belongs to `recover-head` or
`reseal-state`.

The HTTP shape is optional; the stage semantics, state-root visibility, current
operation visibility, sealed-state visibility, replica visibility, preflight
visibility, reseal visibility, and rollback-stack visibility are not optional.

### `MOTHER-DESIGN-025: local-generation-adoption-and-head-fenced-projection-repair`

Local adoption of an already-authoritative replica generation is a pointer-based
transaction with an exclusive local-adoption scope and a normative state machine.
`sync-state` stages and durably persists a complete immutable candidate,
preserves the existing authority and lineage, and commits only by atomically
switching the local active-generation pointer after an `activation-prepared`
record is durable. Adoption transaction metadata remains independently
addressable outside the swappable generation tree, and startup reconciliation is
pointer-deterministic without post-commit materialization.

Projection repair is separate non-authoritative maintenance. It publishes one
complete immutable projection generation through one atomic durable pointer
switch only when the authoritative local head remains exactly unchanged. Head
movement causes disposal and a bounded retry or `projection-head-changed`, never
unbounded retry or mixed-generation publication.

### Remote access through Coolify call-runners

The Mother API is the control surface, but it is not a public internet API. A
remote operator reaches it through the existing Coolify/Allfather bootstrap
channel and one stable reusable private call-runner service on the target host.

```text
operator
  -> Coolify API
  -> locate the host's stable mother-call-runner service
  -> create it only when the host does not yet have one
  -> place the next structured request envelope
  -> start, restart, or signal the existing runner
  -> runner calls http://mother-control:<port>/v1/... or http://127.0.0.1:<port>/v1/...
  -> target local API durably accepts or rejects the request
  -> runner records transport evidence
  -> service remains available and is reused for the next request
```

The runner service is bounded to one active request by default. Its durable
service identity is host-scoped; individual requests are not represented by
separate Coolify services. Completed requests return the service to an idle or
stopped reusable state.

The runner is transport only. It is safe to manually stop, kill, restart,
quarantine, or explicitly replace it. It MUST NOT hold authoritative Mother
state, active operation state, rollback frames, locks, identity material, route
snapshots, or authoritative request results. Those records live under
`/runtime/state/mother/` and inside the target local API state model.

If the runner dies before durable target acceptance, the operator MAY retry the
same request and idempotency key. If it dies after acceptance, the operator MUST
query the durable request or operation status rather than blindly submit a new
intent. A crash, timeout, lost response, or ambiguous result does not trigger
automatic service deletion. The same runner remains for logs and reconciliation,
then is restarted and reused after the request is resolved.

The call-runner request MUST be structured. It SHOULD NOT expose arbitrary shell
as the normal operator interface. A baseline request envelope is:

```json
{
  "request_id": "call-...",
  "idempotency_key": "idem-...",
  "target": "mother",
  "method": "POST",
  "path": "/v1/operations/rpc-propagate/prep",
  "body": {
    "network": "mainnet"
  },
  "request_hash": "sha256:..."
}
```

The runner MUST restrict `target` to approved local/private services, restrict
paths to Mother or guard API prefixes, and preserve enough transport metadata
for the operator to distinguish transport failure from target rejection. The
target's durable request record, not the runner output, is authoritative after
acceptance.

All mutation requests MUST include an idempotency key and normalized request
hash. Repeating the same request with the same key and hash MUST return the same
request or operation record or continue observing the same operation. Reusing
the key with a different request hash MUST fail with `idempotency-conflict`.

### Mother API implementation updates

Updating Mother code is not a topology operation. Operators MAY replace the
Mother compose, restart the Mother container, or install a new mounted API
implementation without creating a topology rollback stack, provided no live
topology/runtime mutation is being requested by that update.

The update safety rule is state externality:

```text
Container/code MAY change.
Authoritative Mother state remains under /runtime/state/mother/.
```

After every start, the Mother API MUST:

- report its implementation version and supported state schemas;
- report the mounted durable state root;
- validate that it can read the current identity, operation, rollback, route,
  topology, guard, lock, and version records;
- refuse mutating actions if it cannot understand the mounted state schema;
- keep read-only status/diagnose endpoints available when possible so the
  operator can see why mutation is refused.


## Three-stage mutation contract

Every authoritative mutating Mother command MUST be run as a staged operation:

```text
prep
do
finalize
```

Until its irreversible commit point, the operation MUST also accept:

```text
rollback
```

The ordinary network-mutation commit point is the authoritative
`pending-action-finalized` network-journal transition. `sync-state` uses the
local active-generation pointer switch defined by `MOTHER-DESIGN-025` instead.
`repair-projections` is not an authoritative mutation and is the sole documented
one-shot exemption; it MUST satisfy the pinned-head atomic maintenance contract
in `MOTHER-DESIGN-025`.

This staged-authority boundary is the most important Mother boundary.

### `prep`

`prep` is the only stage that interprets operator intent.

`prep` MUST:

- run read-only discovery;
- classify the current state;
- validate that the requested operation is coherent;
- calculate the exact desired target state;
- calculate the exact mutation steps;
- declare the complete mutation scope, prestate capture method, restore
  operation, and rollback verification contract for every step that `do` can
  perform;
- acquire logical ownership of every affected scope;
- write an immutable prepared operation record;
- print the plan, risks, affected scopes, required confirmations, and rollback
  behavior.

`prep` MUST NOT:

- stop validators;
- start validators;
- vote;
- write QBFT config;
- clear lifecycle markers;
- modify Coolify services;
- mutate service volumes;
- change container environment;
- delete anything;
- recreate anything.

The only live side effect allowed in `prep` is writing the Mother operation
ledger and lock records.

### `do`

`do` executes exactly the already-prepared operation.

`do` MUST:

- load the prepared operation by operation ID;
- confirm the operation is still active for its declared scopes;
- freshly revalidate the frozen expected head, expected replica set, and full
  clean-state barrier;
- acquire or resume the same durable operation reservation on every expected
  replica and persist the exact full-set certificate;
- commit and fully replicate the certified `pending-action-opened` successor
  before dispatching any live infrastructure mutation;
- refuse if the live state has drifted beyond the prepared preconditions unless
  the prepared operation explicitly declares that drift acceptable;
- perform only the mutation steps recorded in the prepared operation;
- perform runtime mutations through Mother, guard, and routing APIs instead of
  hidden compose replacement;
- include the current operation owner and accepted current-head writer
  certificate in every live mutation request;
- obtain a new exact full-set successor certificate before every authoritative
  network-journal head replacement;
- capture the complete current prestate and durably commit an
  `armed-provisional` frame before each mutating substep;
- refuse the mutation if the prestate cannot be captured completely or the
  provisional frame cannot be persisted;
- write a checkpoint before dispatch;
- apply the prepared typed mutation using the existing provisional frame;
- freshly verify the complete postcondition and active invariant set;
- commit `step-applied-verified-and-promoted` before exposing the frame as an
  active rollback-stack item or beginning the next substep;
- leave the operation in a state that can be finalized, remediated, or rolled
  back.

`do` MUST NOT:

- reinterpret operator intent;
- discover a different desired topology;
- add newly found services to the operation;
- remove missing services from the operation;
- change the operation kind;
- widen scope;
- switch from reseal to restore;
- switch from add to remove;
- silently call another lifecycle path;
- recapture prestate over a failed or partially applied result;
- promote a frame while its forward step remains failed or unverified;
- dispatch live mutation while reservation acquisition is partial, split,
  canceled, stale, or otherwise lacks one current receipt from every expected
  replica.

If a step fails during `do`, Mother MUST leave its frame provisional, enter
`remediation-required`, and report the unresolved step, participant evidence,
completed rollback layers, pop-able range, and these remediation choices:

```text
mother <kind> do <network>                         # retry/resume existing frame
mother rollback <network> --all
mother rollback <network> --count <n>
mother rollback <network> --through <layer-id>
```

Retry/resume uses the same provisional frame. If the desired poststate already
holds, Mother MAY freshly verify and promote it. If the state is a recognized
partial result, Mother MAY retry the prepared mutation. If neither the prestate
nor a recognized partial/desired state can be proven, retry MUST be refused.

### `finalize`

`finalize` proves that the prepared operation reached its declared final state.

`finalize` MUST:

- run the operation's postcondition checks;
- verify that all mutation checkpoints are complete;
- verify that no armed provisional frame remains unresolved;
- verify that the desired state matches the actual state;
- append a `frame-close-prepared` record for every promoted active rollback frame
  to the immutable rollback journal;
- verify those rollback-journal records are durable;
- commit `finalization-prepared` in the action journal with exact rollback and
  pending-network-state references;
- construct and obtain a full-set exact-successor certificate for
  `pending-action-finalized`;
- commit that certified transition in the authoritative network journal,
  promoting the pending desired topology to finalized topology and clearing the
  pending action;
- immediately close rollback and enter `finalized-replication-pending` once that
  authoritative network-journal commit succeeds;
- retain all active scope ownership and block ordinary mutation while
  `finalized-replication-pending`;
- replicate and verify that exact network-journal head on every expected replica;
- append or verify the action-journal `action-finalized` mirror;
- clear the active rollback-stack projection only after the authoritative
  network-journal finalization commit;
- enter `finalized` and release active scope and successor-reservation ownership
  only after every expected replica durably acknowledges the exact finalization
  head and confirms durable reservation release.

A failure before the authoritative network-journal finalization commit leaves the
operation in `finalize-failed`; rollback remains available. A failure or
interruption after that commit MUST NOT return to `finalize-failed` and MUST NOT
offer rollback. It leaves the operation in `finalized-replication-pending`, where
only acknowledgement retry, diagnosis, and explicit replication remediation are
allowed.

`finalize` MUST NOT perform hidden repair. If postconditions fail before the
authoritative finalization commit, `finalize` MUST leave the operation open and
report the allowed next commands:

```text
mother <kind> do <network>                         # retry/resume
mother rollback <network> --all
mother rollback <network> --count <n>
mother rollback <network> --through <layer-id>
```

### `rollback`

`rollback` is valid for every prepared operation only until the authoritative
network-journal finalization entry commits. Replica acknowledgement can still be
pending after that boundary, but rollback is already permanently closed.

Mother MUST first inspect both:

```text
the unresolved armed provisional layer, when present
the completed promoted rollback stack
```

The remediation report lists the provisional participant frames, every promoted
layer in strict LIFO order, the contiguous range that is currently pop-able, and
the reason any layer is blocked.

Rollback modes are:

```text
--all
  restore the unresolved provisional layer, then every promoted layer

--count <n>
  restore the unresolved provisional layer, then pop exactly n completed
  top layers

--through <layer-id>
  restore the unresolved provisional layer, then pop the contiguous completed
  stack through the named layer
```

Arbitrary middle-layer rollback is forbidden. A distributed layer is one logical
stack item even when it has participant frames on multiple hosts.

`rollback` MUST:

- resolve the current active operation from Mother's current-operation pointer
  when the operator does not provide an operation ID;
- prove that any supplied operation ID matches the current operation for the
  requested scope;
- load the same prepared operation record and acquire its required locks;
- retain and revalidate the same distributed successor-reservation owner
  throughout rollback;
- cancel partial pre-mutation reservations through full-set durable tombstones,
  or use the ordinary certified rollback transitions after any successor or live
  mutation was accepted;
- restore any unresolved provisional layer first when rollback is requested;
- freshly verify that provisional layer's complete prestate on every required
  participant;
- commit `provisional-restored-verified` and close it without promotion;
- peek promoted stack layers in reverse order without removing them early;
- restore the complete recorded prestate of every requested top layer;
- verify the exact rollback target state on every required participant;
- append every restore attempt and verification result to the immutable rollback
  journal;
- remove a promoted frame or distributed layer only after durable
  `restored-verified`;
- leave a failed or unverifiable layer at the top and stop before processing
  lower layers;
- recompute active invariants and the safe resume point after partial rollback;
- mark the whole operation `rolled-back` only when no provisional frame and no
  promoted frame remain, the requested mode was `--all`, the rolled-back
  network head is present on every expected replica, and every replica confirms
  durable successor-reservation release;
- otherwise leave the operation open in `remediation-required`.

Rollback is not an operator-authored command list. Mother owns the rollback plan,
complete recorded prestates, provisional frame, promoted stack ordering,
participant membership, immutable rollback journal, and verification of every
restore. The operator chooses how far to unwind, not arbitrary low-level undo
commands.

`rollback` MUST be conservative. If it cannot safely undo a layer, it MUST say
why, preserve the layer, and leave a clear remediation report. It MUST NOT
pretend that a partial rollback is clean.

After `finalize`, rollback is no longer a stage of the completed operation.
Unused promoted frames have durable `frame-close-prepared` records referenced by
the committed network `pending-action-finalized` entry and are no longer
executable. Changing the result of a finalized operation requires a new `prep`.

#### Rollback action stack and rollback journal

Every mutation step that can affect live state MUST declare its complete mutation
scope and have a corresponding frame durably armed as provisional before the
mutation is allowed to execute. The provisional frame set, promoted active stack,
and rollback journal MUST all be inspectable through the Mother API.

The rollback frame stores complete prestate, not merely an inverse command:

```json
{
  "frame_id": "0003-publish-public-route",
  "stage_created": "do",
  "status": "armed-provisional",
  "scope": "route:mainnet-rpc:coolify-a",
  "target_generation": 17,
  "forward_action": {
    "kind": "route.publish",
    "target": "mainnet-rpc.greatlibrary.io"
  },
  "prestate": {
    "exists": true,
    "canonical_hash": "sha256:...",
    "payload_ref": "rollback/prestate/operation-123/0003.json"
  },
  "restore_action": {
    "kind": "route.restore-complete-prestate",
    "target": "mainnet-rpc.greatlibrary.io"
  },
  "verification": {
    "expected_prestate_hash": "sha256:..."
  }
}
```

Forward execution is:

```text
capture complete prestate
commit armed-provisional
apply mutation
freshly verify desired poststate
commit applied-verified-and-promoted
continue
```

Only successfully promoted frames are pushed in forward execution order and
restored in reverse execution order. A failed current frame remains provisional
above that stack.

Mother MUST NOT begin a forward action without the provisional frame. It MUST NOT
continue to the next forward action until the current frame is verified and
promoted.

Rollback MUST peek rather than pop. The top promoted frame remains active while
Mother restores and verifies its prestate. Mother MUST NOT remove that frame from
the active stack before exact restoration is `restored-verified` and durably
journaled. A provisional frame is similarly retained until its prestate is
verified, but it closes without being promoted or popped.

If restore fails, is interrupted, or cannot be verified, Mother appends the
attempt to the rollback journal and retains the frame or layer. Lower completed
layers are not processed. Re-running rollback retries the same idempotent
restore.

Finalization closes the rollback window. It is forbidden while any provisional
frame remains. Before clearing the promoted stack, `finalize` appends
`frame-close-prepared` records for every unused promoted frame and verifies the
rollback-journal head is durable. It then commits `finalization-prepared` in the
action journal and `pending-action-finalized` in the network journal with exact
cross-journal references. After that network finalization commit, frames from the action MUST NOT be
executed.

Rollback frames, promotion events, and restore attempts are durable Mother
state. They MUST NOT live only in a local shell script, terminal output,
transport response, or transient container memory. The action-specific rollback
journal is append-only and separate from the global network-state journal.


## Active operation conflict rule

Mother is told what is going to happen during `prep`. Until that operation is
finalized or rolled back, Mother MUST treat that prepared instruction as the
active truth for its declared scopes.

A scope MAY be:

```text
network:mainnet
host:coolify-a
service:mainneta-super1
validator:0xb5...
coolify-service-uuid:<uuid>
successor-reservation:mainnet
```

Every mutating operation declares the scopes it owns. An authoritative network
mutation also owns `successor-reservation:<network>` from the first remote claim
until full rollback or full finalized acknowledgement and durable release. While
any of those scopes has an active non-finalized operation, or while any expected
replica reports an unresolved reservation for the scope, Mother MUST reject
conflicting commands.

For example, if Mother has an active prepared operation:

```text
operation_id: reseal-mainnet-001
kind: reseal-qbft
scopes:
  - network:mainnet
  - service:mainneta-super1
  - service:mainnetc-super1
state: do-complete-pending-finalize
```

Then these MUST be rejected:

```text
mother remove-node prep mainnet --node mainneta-super1 --mode soft
mother add-node prep mainnet --node mainnetd-super1 --host coolify-d --mode soft
mother reseal-qbft prep mainnet --nodes mainnetc-super1
mother restore-service prep mainnet --node mainneta-super1
```

The error MUST say the next allowed commands:

```text
Active operation blocks this command:
  operation_id: reseal-mainnet-001
  kind: reseal-qbft
  state: do-complete-pending-finalize
  scopes: network:mainnet, service:mainneta-super1, service:mainnetc-super1

Allowed next commands:
  mother reseal-qbft finalize mainnet --operation-id reseal-mainnet-001
  mother rollback mainnet --operation-id reseal-mainnet-001
```

If the user repeats the same `prep` with the same idempotency key and same intent,
Mother MAY return the existing operation. If the user repeats the same operation
without the idempotency key, Mother SHOULD still detect that an equivalent active
operation exists and ask the operator to use the existing operation ID.

The rule is:

```text
No second story starts until the first story has been finalized or rolled back.
```

If Mother is told a different story while a current operation exists for an
overlapping scope, Mother MUST NOT reinterpret the new command as a correction.
It MUST answer with the current operation ID, its stage, and the exact allowed
next commands. The normal allowed next commands depend on the current action state:

```text
ready-to-finalize:
  mother <kind> finalize <network> [--operation-id <id>]
  mother rollback <network> --all

remediation-required:
  mother <kind> do <network>                         # retry/resume
  mother rollback <network> --all
  mother rollback <network> --count <n>
  mother rollback <network> --through <layer-id>

reservation-incomplete:
  mother <kind> do <network>                       # retry/resume exact claim
  mother rollback <network> --all                  # full-set tombstone cancellation

finalized-replication-pending:
  mother diagnose <network>
  mother <kind> finalize <network>                  # resume acknowledgement only
```

The operator SHOULD NOT need to pass `--operation-id` for these commands because
Mother already knows the current operation for the network and scopes. When an
operation ID is shown, it is for auditability and cross-checking, not because
the operator is responsible for remembering the rollback target.


## Topology probe contract

The hard problem in Mother is not merely starting or stopping services. The hard
problem is knowing whether the observed service topology, process topology,
validator identity topology, QBFT consensus topology, RPC routing, and Hub/FDB
topology agree well enough to add a node, remove a node, or repair drift.

Mother therefore treats topology probing as a first-class control-surface
operation. A topology probe is read-only and answers: "What exists right now, what
does each piece believe, and which lifecycle action is safe to prepare next?"

A topology probe MUST NOT mutate service definitions, deploy new probes by
rewriting existing services, restart containers, submit validator votes, write
genesis files, clear markers, enable routes, disable routes, delete services, or
infer intent. It only observes and classifies.

### What topology probing detects

A Mother topology probe MUST detect at least these facts for every relevant
network and super-node service:

- Coolify service identity:
  - service name;
  - service UUID;
  - Coolify host;
  - project and environment;
  - running/deploying/stopped/deleted status;
  - configured private ports;
  - configured public routes;
  - whether public routes are currently enabled, disabled, or absent.
- Guard identity:
  - guard URL;
  - guard reachability;
  - reported `network_key`;
  - reported service/cell identity;
  - reported topology metadata source and freshness.
- Local runtime processes:
  - FoundationDB desired/running/listening state;
  - Hub desired/running/health state;
  - validator RPC desired/running state;
  - JSON-RPC reachability;
  - peer count;
  - current block number;
  - whether block height is advancing.
- Validator identity:
  - whether the validator key exists;
  - validator address derived from that key;
  - whether the same service reports the same validator address over repeated
    probes;
  - whether the validator address is already in the QBFT set.
- QBFT consensus topology:
  - validator set according to every reachable validator RPC;
  - whether all reachable validators agree on the same set;
  - whether the set contains unknown validators not mapped to known services;
  - whether known services are missing from the set;
  - whether removing a target would remove the final validator.
- Lifecycle marker topology:
  - admission markers;
  - removal markers;
  - removal handoff markers;
  - reseal markers;
  - restore markers;
  - whether each marker is active, complete, stale, contradictory, or irrelevant
    to the requested operation.
- Mother operation topology:
  - active prepared operation IDs;
  - scopes owned by active operations;
  - stage reached by each active operation;
  - allowed next commands for each active operation.

A topology probe MUST classify each service into explicit states. Recommended
classifications:

```text
absent
service-present-stopped
service-present-starting
service-present-running-no-guard
guard-ready-no-validator
validator-running-not-in-qbft
validator-running-in-qbft
validator-running-in-qbft-public-routes-disabled
validator-running-in-qbft-public-routes-enabled
stale-admission
stale-removal
stale-reseal
split-brain
unknown-validator-in-qbft
service-missing-for-qbft-validator
```

The classifications are not decorative. They decide which `prep` operations are
allowed and which operations MUST be refused until rollback, finalize, reseal, or
restore resolves the contradiction.

### Mother as topology authority

Mother has four related views of topology and network state:

- **observed topology**: what probes see right now;
- **finalized topology**: the last operator-accepted intended topology;
- **pending distributed state**: the replicated action, desired topology,
  applied phases, rollback rights, and remediation state that are not finalized;
- **sealed replicated network state**: the complete journal checkpoint/head and
  state hash covering both finalized topology and pending distributed state.

Observed topology is evidence. Finalized topology is accepted intent. Pending
state explains reversible live differences from that intent. The seal proves
which complete network state the local head and every expected replica agree on.
These views MUST NOT silently overwrite one another.

Before `prep` or any mutating command trusts network facts, Mother runs the
sealed-state preflight. If the remote replicas agree and the local head is stale,
the command refuses mutation and directs the operator to an explicit staged
`sync-state`, replacement-head recovery, or reseal path. `sync-state` records
its local adoption transaction and commits only through the atomic
active-generation pointer switch. `diagnose` only reports the mismatch and never
performs that write.
`repair-projections` is allowed only when replay of the already-authoritative
local lineage proves that no authoritative adoption is occurring. If remote
replicas disagree, omit a pending action, or the state is wedged, normal mutation
is refused until remediation or `reseal-state` creates a new explicit seal.

`prep` compares observed topology with finalized topology and records a planned
transition. After the clean-state barrier, `do` obtains the exact full-set
successor certificate and uses its first certified transition to create and
replicate `pending_action` on every expected replica. Only then does `do` execute
that planned transition and
commits every meaningful phase change to the pending state. `finalize` proves
the observed topology reached the pending desired state and commits
`pending-action-finalized`, which advances finalized topology and clears the
pending action. `rollback` journals the reverse progress and, after full verified
restoration, clears the pending action without advancing finalized topology.

Until `finalize` runs, finalized topology MUST NOT pretend the operation is
complete, but the replicated network state MUST still describe what is physically
applied and reversible. If another command is requested for an overlapping
scope, Mother MUST reject it and print the active operation plus the allowed
`finalize`, retry/resume, or rollback commands.

### Topology change commands and modes

Validator membership, RPC routing, Hub/FDB topology, and service lifecycle are
phases of `add-node` and `remove-node`.

- `add-node` creates or repairs the service, installs its reserved identity,
  admits the validator, publishes RPC routing, publishes Hub/FDB topology, and
  leaves the complete distributed action rollback-capable until the documented irreversible commit point.
- `remove-node` withdraws Hub/FDB topology and RPC routing, removes validator
  membership, detaches/removes the service, and leaves the complete distributed
  action rollback-capable until the documented irreversible commit point.
- `reseal-qbft` repairs the entire selected QBFT topology in place and is not an
  ordinary node lifecycle command.

Mother supports three topology-change modes. The operator's primary node command
MUST choose or imply the mode during `prep`; `do` MUST NOT switch modes.

Initial topology change:

```text
Used only by add-node when committed chain topology is empty.
Installs Mother-owned first-genesis material from /runtime/state/mother/identity.private.yaml.
Starts the first validator from the reserved identity.
No live QBFT vote exists because there are no prior validators.
```

Soft topology change:

```text
Use the live QBFT add/remove voting path.
The chain keeps running.
Existing validators vote to admit or remove a validator.
```

Hard topology change:

```text
Use an offline in-place topology repair.
Selected validators are quiesced.
Identical QBFT config/topology is written in place.
Validators are restarted and agreement is verified.
```

Soft mode is for healthy consensus. Hard mode is for explicit maintenance or
drift repair. Initial mode is for the first node in an empty topology. A hard
topology phase is not service deployment. It MAY stop and restart validator
subprocesses, but it MUST NOT delete/recreate unrelated Coolify services, rebuild
images, or replace compose.

### Route gating

Public routes are topology outputs, not proof of consensus membership. Route
changes are typed Mother routing operations with distributed rollback frames,
not hidden compose side effects.

For `add-node`, the new node remains private until its service and identity are
healthy and validator admission is proven. RPC routing is then reconciled and
verified, followed by the network-wide Hub/FDB topology. For `remove-node`,
Hub/FDB and RPC dependencies are withdrawn before validator membership and
service removal. Every route layer remains rollback-capable until the complete
node action is finalized.

Mother MUST distinguish:

```text
internal candidate ready
validator membership updated
RPC route topology updated
Hub/FDB topology updated
service topology removed
```

These are separate verified facts within one user-facing action, not separate
commands and not one readiness flag.

## Operation states

A Mother mutating operation uses explicit states:

```text
prepared
reserving-successor
reservation-incomplete
doing
remediation-required
do-complete-pending-finalize
finalizing
finalize-failed
finalized-replication-pending
finalized
rolling-back
rollback-failed
rolled-back
```

Only these states accept mutating or remediation commands:

```text
prepared:
  do, rollback --all

reserving-successor:
  retry/resume the exact operation, intent, predecessor, and successor claim
  rollback --all through full-set durable reservation cancellation
  no live mutation or journal-head replacement is authorized

reservation-incomplete:
  do or retry/resume the exact existing claim
  rollback --all through full-set durable reservation cancellation
  no different operation, intent, predecessor, or successor is accepted

doing:
  retry/resume after interruption
  rollback under the same reservation, including a certified pending-action
  rollback when no live frame has yet been armed

remediation-required:
  retry/resume
  rollback --all
  rollback --count <n>
  rollback --through <layer-id>

do-complete-pending-finalize:
  finalize, rollback --all, rollback --count <n>, rollback --through <layer-id>

finalize-failed:
  valid only when no authoritative network-journal finalization entry exists
  finalize retry
  retry/resume when an unverified mutation is identified
  rollback choices

finalized-replication-pending:
  rollback is closed
  acknowledgement retry, diagnosis, and explicit replication remediation only
  all active scopes remain owned and ordinary mutation remains blocked

rollback-failed:
  rollback retry, inspection, explicit rectification

finalized:
  no further stage commands; create a new operation for further changes

rolled-back:
  no further stage commands; create a new operation for further changes
```

A failed or split reservation acquisition maps to `reservation-incomplete`
before any live mutation. A failed or unverified live step maps to
`remediation-required`; its frame remains provisional until it is successfully
verified and promoted or its prestate is restored and the frame is closed.
Read-only diagnosis is always allowed and MUST show the per-replica reservation
owner and claim distribution, certificate status, cancellation tombstones,
provisional layer, promoted stack, participant evidence, pop-able range, and
exact allowed commands.

`sync-state` uses a separate normative local-adoption state machine because it
does not create or finalize a network mutation:

```text
sync-prepared:
  allowed: do, rollback

sync-staging:
  allowed: retry/resume, rollback

sync-ready-to-activate:
  allowed: finalize, rollback

sync-activation-failed:
  old pointer active:
    allowed: finalize retry, rollback
  candidate pointer active:
    allowed: reconcile to sync-committed
    rollback closed

sync-committed:
  terminal
  release local-adoption scope

sync-rolling-back:
  allowed: rollback retry

sync-rolled-back:
  terminal
  release local-adoption scope
```

The local-adoption scope ownership table is normative:

```text
scope owned:
  sync-prepared
  sync-staging
  sync-ready-to-activate
  sync-activation-failed
  sync-rolling-back

scope released:
  sync-committed
  sync-rolled-back

conflicts while owned:
  another sync-state
  ordinary authoritative mutation
  recover-head
  reseal work
  repair-projections
```

Before the active-generation pointer switch, `sync-state` rollback discards the
staged generation and preserves the pinned local prestate. After the pointer
switch, rollback is closed and crash recovery MUST load the already-complete
generation named by that pointer and reconcile the operation record to
`sync-committed`. The local-adoption scope MUST remain owned through
`sync-activation-failed` and MUST be released only in `sync-committed` or
`sync-rolled-back`. `sync-state` MUST NOT enter
`finalized-replication-pending`.

## Script boundaries

### `mother_diagnose.py`

Read-only only.

Purpose:

- observe Coolify service topology;
- observe guard reachability;
- observe runtime process topology;
- observe validator identity topology;
- observe QBFT consensus topology;
- observe lifecycle markers;
- observe active Mother operation state;
- classify contradictions.

Forbidden:

- deploy probes that mutate service definitions;
- update Coolify services;
- stop/start processes;
- write config;
- clear markers;
- create lifecycle operation records unless explicitly invoked as part of `prep`.

Output:

```text
diagnosis-report.json
```

### `mother_plan.py`

Read-only planner.

Purpose:

- consume a diagnosis report;
- evaluate proposed operator intent;
- calculate affected scopes;
- detect active operation conflicts;
- build a candidate operation plan;
- show risks and rollback model.

`mother_plan.py` MAY be used internally by `prep`, but it MUST NOT mutate live
infrastructure.

### `mother_reseal_state.py`

Complete network-state replica recovery transaction.

Purpose:

- compare the active local head state with sealed complete-state replicas on
  the remote machines;
- recover when local state is stale but the network replicas agree;
- create an explicit new seal when remote replicas disagree or the network is
  wedged;
- push the chosen complete network-state seal to the replicas;
- retain superseded conflicting seals for audit.

Stage contract:

```text
mother reseal-state prep mainnet --from-live --reason "..."
mother reseal-state do --operation-id <id>
mother reseal-state finalize --operation-id <id>
mother rollback mainnet
```

`prep` for reseal-state MUST capture:

- local seal metadata;
- every reachable remote seal metadata record;
- unreachable replicas;
- selected source of truth, if any;
- live guard, topology, route, and service facts used to justify the reseal;
- desired new topology epoch and state hash;
- exact replica files to write;
- exact superseded seal markers to write;
- rollback behavior for replicas that have already accepted the new seal.

Forbidden:

- using reseal-state to silently change validator membership;
- using reseal-state as a replacement for the validator-membership phase of
  `add-node` or `remove-node`;
- deleting conflicting seal records instead of marking them superseded;
- continuing another mutating command after a mismatch without first completing
  or refusing reseal-state.

Rollback expectation:

- restore each touched replica to its captured pre-reseal seal when possible;
- if some replicas already moved forward and cannot be restored, report the
  exact split and leave normal mutations blocked until a new reseal-state plan
  is prepared.

### `mother_reseal_qbft.py`

In-place validator configuration transaction.

Purpose:

- make a selected set of existing services authoritative for QBFT topology;
- stop only validator subprocesses inside those services;
- write identical QBFT config/genesis/topology to those existing service volumes;
- clear stale add/remove lifecycle markers;
- restart validator subprocesses;
- verify all selected validators agree on the desired validator set.

Stage contract:

```text
mother reseal-qbft prep ...
mother reseal-qbft do --operation-id <id>
mother reseal-qbft finalize --operation-id <id>
mother rollback mainnet
```

Forbidden:

- deleting Coolify services;
- recreating Coolify services;
- rebuilding images;
- replacing compose;
- changing service names;
- changing validator keys;
- using live QBFT voting;
- calling add/remove helpers;
- deriving desired validators from service count after `prep`.

Rollback expectation:

- if `do` has backed up old QBFT config and data, restore the backups;
- restart validators in the pre-operation mode;
- restore stale marker files only if they were captured in the prepared rollback
  snapshot;
- never invent pre-operation state that was not captured in `prep`.

### `mother_add_node.py`

Complete distributed node addition.

Purpose:

- create or repair the target super-node service;
- install the reserved identity from `/runtime/state/mother/identity.private.yaml`;
- bring the node up as a healthy private candidate;
- admit it to the prepared QBFT validator set;
- reconcile host-local canonical RPC routing;
- reconcile Hub/FDB topology on every affected node;
- keep the entire action rollback-capable until the documented irreversible commit point.

Stage contract:

```text
mother add-node prep mainnet --node <service> --host <host> --mode initial|soft|hard
mother add-node do mainnet
mother add-node finalize mainnet
mother rollback mainnet
```

`prep` MUST run the full-network clean-state barrier and record:

- every expected Coolify host and network participant;
- current committed and observed service, validator, RPC, and Hub/FDB topology;
- target service name, host, ports, and route reservations;
- reserved validator identity;
- desired validator set, RPC route graph, and Hub/FDB topology;
- selected mode;
- for soft mode, the proposal ID, frozen voter/observer manifest, before and
  desired validator-set hashes, and baseline block number;
- every distributed mutation scope;
- the ordered distributed rollback plan.

`do` performs only the prepared action:

1. capture target service, identity, and runtime prestates;
2. create/repair the service and establish a healthy private candidate;
3. capture validator-membership prestates across the frozen voter/observer
   manifest;
4. perform the prepared initial, guard-mediated soft-vote, or hard change and
   verify complete set agreement plus post-change block progress;
5. capture and reconcile RPC routing on every affected host;
6. capture and reconcile Hub/FDB topology on every affected node;
7. run full guard verification and leave the action pending finalize.

Forbidden:

- inventing validator identity at runtime;
- publishing RPC before validator admission is proven;
- applying Hub/FDB topology before RPC reconciliation succeeds;
- beginning while any expected host has unresolved work;
- treating a majority response as full-network success;
- creating a separate hidden topology operation;
- considering `vote-requested` to be success.

`finalize` MUST freshly prove:

- service and identity match the prepared target;
- all validators report the desired effective validator set;
- block production progresses;
- each affected RPC host matches the desired owned route graph;
- every required node matches the desired Hub/FDB topology epoch;
- every distributed rollback frame is present and consistent;
- all expected replicas agree on the resulting network state.

### `mother_remove_node.py`

Complete distributed node removal.

Purpose:

- remove the target from Hub/FDB topology on every affected node;
- remove the target from host-local RPC route graphs;
- remove the validator from QBFT using the prepared mode;
- detach, disable, archive, or remove the target service;
- keep the entire action rollback-capable until the documented irreversible commit point.

Stage contract:

```text
mother remove-node prep mainnet --node <service> --mode soft|hard
mother remove-node do mainnet
mother remove-node finalize mainnet
mother rollback mainnet
```

`prep` MUST run the full-network clean-state barrier and record:

- explicit target service and validator address;
- all surviving services, validators, RPC destinations, and Hub/FDB participants;
- current and desired validator sets;
- current and desired RPC route graphs;
- current and desired Hub/FDB topology;
- selected mode and final service policy;
- for soft mode, the proposal ID, frozen voter/observer manifest, before and
  desired validator-set hashes, and baseline block number;
- every distributed mutation scope;
- the ordered distributed rollback plan.

`do` performs only the prepared action:

1. capture Hub/FDB prestates on every affected node;
2. reconcile Hub/FDB topology without the target and verify all survivors;
3. capture RPC route prestates on every affected host;
4. reconcile RPC routing without the target and verify surviving public service;
5. capture validator-membership prestates across the frozen voter/observer
   manifest while the target remains running;
6. perform the prepared guard-mediated soft-vote or hard topology change and
   verify complete set agreement plus post-change block progress;
7. capture target service/runtime/identity prestate;
8. detach, disable, archive, or remove the target exactly as prepared;
9. run full guard verification and leave the action pending finalize.

Forbidden:

- removing the final validator by accident;
- inferring the target from ordinal or service count;
- deleting the service before Hub/FDB, RPC, and validator dependencies are removed;
- treating removal of the final validator as an ordinary removal before
  `MOTHER-OPEN-017` defines explicit zero-validator authorization, identity
  preservation, and recovery bootstrap.
- hiding a hard reseal inside an ordinary soft remove;
- beginning while any expected host has unresolved work;
- treating partial distributed completion as success.

`finalize` MUST freshly prove:

- target is absent from the effective validator set;
- survivors agree and block production progresses;
- target is absent from every RPC route graph;
- every node reports the desired Hub/FDB topology;
- target service state matches the prepared removal policy;
- every distributed rollback frame is present and consistent;
- all expected replicas agree on the resulting network state.

### Compatibility aliases

Mother MAY expose `add-validator` and `remove-validator` only as aliases that
invoke the same complete distributed `add-node` or `remove-node` action engine.
They MUST NOT expose a partial validator-only workflow when service, RPC, or
Hub/FDB state is affected.

### `mother_restore_service.py`

Coolify service repair only.

Purpose:

- recreate or repair a missing Coolify service from explicit saved service
  identity and volume/key expectations.

Stage contract:

```text
mother restore-service prep ...
mother restore-service do --operation-id <id>
mother restore-service finalize --operation-id <id>
mother rollback mainnet
```

Forbidden:

- changing QBFT validator membership;
- resealing genesis;
- running live add/remove votes;
- inferring validator identity from service name alone.

Rollback expectation:

- if a new service was created but not finalized, stop and remove only that newly
  created service;
- never delete pre-existing volumes unless the prepared rollback plan explicitly
  proves they were created by this operation.

## Sealed-state preflight and reseal

The first step of any Mother command that talks to a network is sealed-state
preflight. This preflight is read-only classification and comparison; it is not
state synchronization and it does not mutate live infrastructure or authoritative
local state.

Preflight MUST collect each reachable replica's committed-state metadata:

```text
network_key
topology_epoch
state_hash
previous_state_hash
sealed_at
sealed_by
committed_action_id
schema_version
modified_at
```

Then it classifies the state:

```text
local-current:
  local epoch/hash equals the agreed remote epoch/hash.

local-stale-network-agrees:
  remotes agree unanimously on a newer compatible generation than local.
  Ordinary mutation stops. The operator runs staged `sync-state`; preflight
  itself does not copy or activate remote state.

network-replica-mismatch:
  remotes disagree by epoch or hash.
  Normal mutation is refused.

wedged:
  a seal is missing, equal epochs have different hashes, required operation
  records are missing, live guard/route facts contradict the seal, or the state
  cannot be proven.
  Normal mutation is refused.
```

Ordinary commands MUST NOT continue unless the classification is
`local-current`.
`local-stale-network-agrees` requires staged `sync-state` before ordinary
mutation. `network-replica-mismatch` and `wedged` require explicit recovery or
`reseal-state`.

`reseal-state` is the explicit recovery command for committed-state ambiguity. It
MUST be planned and executed like any other Mother operation. Its plan MUST show
which local/remote seals were found, which live facts were used, which state is
being chosen as the new committed state, what superseded seals will be retained
for audit, and which replicas will receive the new seal.

Reseal MUST NOT be an automatic side effect of `diagnose`, `add-node`, or
`remove-node`. Those commands MAY report that reseal is required and print the
exact reseal command, but they MUST NOT invent a new committed state while
performing another operation.

## Lifecycle state machine

The old lifecycle model was:

```text
observe -> mutate -> wait -> patch next error
```

Mother's lifecycle model is:

```text
diagnose -> prep -> do -> do-complete-pending-finalize -> finalize
                 |
                 +-> remediation-required
                       +-> retry/resume
                       +-> rollback --all
                       +-> rollback --count/--through
```

`diagnose` is read-only and can be run at any time. `prep`, `do`, `finalize`, and
`rollback` are operation stages bound to an operation ID. Remediation reuses that
same action and its existing provisional frame; it does not create a new story.

### Diagnose

Mother reads the world and reports it.

Inputs:

- network key;
- optional service filters;
- optional host filters.

Outputs:

- service inventory;
- guard status;
- validator identities;
- QBFT validator sets;
- block heights;
- lifecycle markers;
- active Mother operation records;
- classification.

No locks are acquired and no mutation occurs.

### Prep

Mother turns operator intent into an operation record.

Inputs:

- command kind;
- network key;
- explicit target nodes/services;
- operator-provided options;
- idempotency key;
- confirmation flags.

Outputs:

- operation ID;
- affected scopes;
- immutable desired state;
- preconditions;
- mutation steps;
- rollback steps;
- postconditions;
- risk report.

Prep owns the affected logical scopes until full rollback completes or the
operation reaches `finalized`. At the beginning of `do`, an authoritative
network mutation additionally acquires the distributed
`successor-reservation:<network>` scope from every expected replica. That
ownership remains held throughout `reservation-incomplete`, remediation,
rollback, and `finalized-replication-pending`.

### Do

Mother performs the prepared mutation.

Inputs:

- operation ID.

Outputs:

- checkpoint stream;
- current operation state;
- next allowed commands.

`do` MUST be restart-aware. Before any live mutation it acquires or resumes the
same full-set operation reservation and certified `pending-action-opened`
successor. If reservation acquisition is partial, rerunning `do` with the same
operation ID retries the exact claims and does not create a new story. After live
work begins, rerunning `do` inspects the existing provisional frame and live
assertions. It MAY
promote an already-complete step, retry a recognized partial state, resume from
the first unpromoted step, or safely report why it cannot continue. It MUST NOT
capture a replacement prestate over the interrupted result. The mandatory
idempotency contract is restore-to-prestate: every provisional or promoted frame
MUST remain safely restorable until its required closing event is committed.

### Finalize

Mother proves completion and closes the operation.

Inputs:

- operation ID.

Outputs:

- final verification report;
- `finalized-replication-pending` or `finalized` status;
- released scopes and successor reservations only after every expected replica
  acknowledges the exact finalization head and durable reservation release;
- finalized operation record.

Finalize is the only success path that makes rollback unavailable. Once the
authoritative network-journal finalization entry commits, the operation enters
`finalized-replication-pending` until every expected replica acknowledges that
exact head; only then is its reported state `finalized`.

### Rollback

Mother undoes or safely backs out a non-finalized operation.

Inputs:

- operation ID.

Outputs:

- rollback checkpoint stream;
- rollback verification report;
- remaining provisional and promoted layers;
- recomputed safe resume point;
- released scopes and a rolled-back operation record only after `--all`
  completes.

Rollback MUST be available after `prep`, during/after `do`, and after failed
`finalize`, until the operation reaches its documented irreversible commit point.
Before live mutation, rollback cancels the exact partial/full reservation through
durable full-set tombstones. After a network successor or live mutation exists,
rollback retains the same operation reservation while it restores and certifies
the rolled-back state. An unresolved provisional
frame is restored and closed before completed layers are popped. A promoted
rollback frame is removed from the active stack only after its prestate
restoration is verified and the result is durably appended to the rollback
journal. Partial rollback leaves the operation open in
`remediation-required`.

## Reseal contract

`reseal-qbft` is an offline, in-place validator configuration repair.

It is used when service inventory and QBFT membership have drifted, when stale
admission/removal state exists, or when the operator intentionally wants the
selected existing services to become the authoritative QBFT topology.

It MUST work from explicit selected services:

```text
mother reseal-qbft prep mainnet --nodes mainneta-super1,mainnetc-super1
mother reseal-qbft do --operation-id <id>
mother reseal-qbft finalize --operation-id <id>
```

`prep` for reseal MUST capture:

- selected service names;
- Coolify hosts and service UUIDs;
- guard URLs;
- validator addresses;
- validator key existence;
- current QBFT validator sets from each reachable node;
- current block heights;
- current local QBFT config paths;
- backup targets;
- stale lifecycle markers;
- desired validator set;
- exact files to rewrite;
- exact rollback files to restore.

`do` for reseal MAY:

- stop validator subprocesses through guard-local runtime control;
- create backups of existing QBFT config and data;
- write the prepared QBFT config/genesis/topology;
- clear stale lifecycle markers captured in the plan;
- restart validator subprocesses.

`do` for reseal MUST NOT:

- rediscover a different desired validator set;
- include a newly running service that was not prepared;
- drop a selected service because it temporarily disappeared;
- delete Coolify services;
- recreate Coolify services;
- mutate compose;
- rebuild images.

`finalize` for reseal MUST prove:

- each selected service is still the same service identity captured during prep,
  unless the prepared plan explicitly allowed a restore-service dependency;
- each selected service still owns the expected validator address;
- every selected validator RPC is reachable;
- every selected node reports the same QBFT validator set;
- the reported QBFT validator set equals the prepared desired validator set;
- block production advances after restart;
- stale add/remove lifecycle markers are inactive or marked complete;
- rollback backups MAY now be retained for audit but are no longer part of an
  active rollback path.

`rollback` for reseal MUST restore the pre-operation local QBFT files and marker
state that `prep`/`do` captured, then restart validators in the previous mode. If
pre-operation state was not captured, rollback MUST refuse to pretend it can
restore it.

## Distributed node lifecycle contract

Mother exposes `add-node` and `remove-node` as the complete user-facing node
lifecycle. Validator admission/removal, RPC reconciliation, Hub/FDB topology,
and service mutation are internal phases of those actions.

```text
add-node:
  create/repair service
  -> establish private candidate
  -> admit validator
  -> reconcile RPC routing
  -> reconcile Hub/FDB topology
  -> verify complete network
  -> pending finalize

remove-node:
  withdraw Hub/FDB topology
  -> withdraw RPC routing
  -> remove validator membership
  -> detach/remove service
  -> verify complete network
  -> pending finalize

reseal-qbft:
  hard full-set in-place topology repair for selected existing services
```

A node action owns the network scope for its full lifetime. Internal phases are
not separately finalizable and do not release their rollback frames.

### Distributed preparation barrier

`add-node prep` and `remove-node prep` MUST fail unless every expected Coolify
host proves:

- it is reachable;
- its committed journal/checkpoint and replayed state agree with the local head;
- it has no unresolved Mother action;
- it has no executable rollback frame;
- it has no provisional guard frame;
- it has no conflicting resource lock;
- it has no unresolved successor reservation owned by another operation;
- it supports the required schema and capabilities, including exact-successor
  claim, receipt, cancellation, and release.

Mother records the exact participant set in the action journal. The participant
set MAY include every current node, every voting validator, every affected
Coolify host, and the target node. The frozen participant set MUST NOT silently drop any participant after
`prep`.

### Distributed rollback semantics

Each internal phase captures complete prestate before mutation. A phase that
touches several hosts or nodes creates one logical distributed provisional layer
containing participant frames.

The layer is not `armed-provisional` until every required participant frame is
durable. It is not promoted onto the completed rollback stack until every
participant freshly verifies the intended forward poststate and Mother commits
`step-applied-verified-and-promoted`.

If forward verification fails, the distributed layer remains provisional above
the completed stack and the action enters `remediation-required`. On rollback, a
promoted layer is not complete until every participant has:

```text
restored the requested complete prestate
freshly verified the restoration assertions
committed restored-verified to its rollback journal
```

The provisional or promoted layer remains in place when any participant fails
or becomes unreachable. Lower completed layers are not attempted.

### `add-node`

`add-node prep` MUST calculate and record:

- the complete target service and identity plan;
- the mode: `initial`, `soft`, or `hard`;
- the current and desired validator sets;
- the current and desired host-local RPC route graphs;
- the current and desired Hub/FDB topology for every node;
- all affected participant and resource scopes;
- the complete ordered distributed rollback plan.

`add-node do` is ordered. Every captured frame or distributed layer is
promoted only after its complete forward poststate is freshly verified; the next
numbered phase MUST NOT begin before promotion commits.

1. Cross the distributed preparation barrier.
2. Acquire the exact full-set operation reservation, certify and fully replicate
   `pending-action-opened`, and retain the reservation beneath the rollback stack.
3. Capture the target service prestate and create or repair the service.
4. Capture identity prestate and install the reserved identity.
5. Capture runtime prestate and establish a healthy private candidate.
6. Capture validator-membership prestate for the frozen voter/observer manifest,
   including the before-set hash and baseline block.
7. Admit the validator using the prepared initial mode, guard-mediated soft-vote
   proposal, or hard mode.
8. Freshly prove all required receipts, network-wide desired-set agreement, and
   post-membership block progress.
9. Capture complete RPC-routing prestate on every affected Coolify host.
10. Reconcile complete host-local canonical RPC backend sets.
11. Freshly prove route ownership, backend membership, Traefik load, and expected
    public chain identity.
12. Capture complete Hub/FDB prestate on every current node, including the new
    node.
13. Reconcile complete Hub/FDB topology on every participant.
14. Freshly prove the same topology epoch, peers, forwarding entries, and
    handoff targets everywhere.
15. Replicate and verify the pending resulting state.
16. Mark the action `do-complete-pending-finalize`.

The active stack after a successful add is:

```text
top
  distributed Hub/FDB topology layer
  distributed/per-host RPC routing layer
  distributed validator-membership layer
  target runtime layer
  target identity layer
  target service layer
bottom
```

Rollback therefore restores Hub/FDB topology first, then RPC routing, then the
prior validator set, and finally the target runtime, identity, and service.

`add-node finalize` MUST freshly verify the complete active assertion set across
all participants. It then closes every rollback layer through the journaled
finalization protocol and commits/replicates the resulting network state.

### `remove-node`

`remove-node prep` MUST calculate and record:

- the explicit target service and validator;
- the mode: `soft` or `hard`;
- surviving validator and service participants;
- the current and desired Hub/FDB topology;
- the current and desired RPC route graphs;
- the current and desired validator sets;
- the final service policy: detached, disabled, archived, or removed;
- all affected participant and resource scopes;
- the complete ordered distributed rollback plan.

`remove-node do` is ordered. Every captured frame or distributed layer is
promoted only after its complete forward poststate is freshly verified; a failed
phase enters remediation and blocks later phases.

1. Cross the distributed preparation barrier.
2. Acquire the exact full-set operation reservation, certify and fully replicate
   `pending-action-opened`, and retain the reservation beneath the rollback stack.
3. Freshly verify that all surviving Hub, RPC, and validator participants are
   healthy enough to carry the resulting network.
4. Capture Hub/FDB prestate on every current node.
5. Reconcile Hub/FDB topology without the target and verify every survivor.
6. Capture RPC-routing prestate on every affected Coolify host.
7. Reconcile complete RPC backend sets without the target and verify surviving
   public RPC service.
8. Capture validator-membership prestate for the frozen voter/observer manifest
   while the target remains running and reachable.
9. Remove the validator using the prepared guard-mediated soft-vote proposal or
   hard mode.
10. Freshly prove all required receipts, target absence, complete desired-set
    agreement, and post-membership block progress.
11. Capture target service, runtime, and identity prestate.
12. Detach, disable, archive, or remove the target exactly as prepared.
13. Replicate and verify the pending resulting state.
14. Mark the action `do-complete-pending-finalize`.

The active stack after a successful removal is:

```text
top
  target service/runtime/identity layer
  distributed validator-membership layer
  distributed/per-host RPC routing layer
  distributed Hub/FDB topology layer
bottom
```

Rollback therefore recreates or restores the target service first, restores the
prior validator set and verifies network health, restores RPC routing, and then
restores the prior Hub/FDB topology. The target is not exposed again before the
runtime and validator membership it depends on are healthy.

`remove-node finalize` MUST freshly prove the complete active assertion set
across all participants. It then closes every rollback layer through the
journaled finalization protocol and commits/replicates the resulting network
state.

### Routing and Hub/FDB assertions

RPC reconciliation is complete only when the affected host guards freshly prove:

```text
owned RPC router/service graph equals desired state
backend set equals the complete desired host-local set
Traefik loaded the intended generation
all required backends are reachable
public endpoint returns the expected chain identity
```

Hub/FDB reconciliation is complete only when every affected node guard freshly
proves:

```text
topology epoch equals the desired epoch
peer set equals the desired peer set
forwarding entries equal the desired entries
handoff targets equal the desired targets
required peer reachability succeeds
```

An exit code, written route file, submitted vote, or successful API response is
not sufficient proof.

### Finalization boundary

After `do` succeeds, the network SHOULD look complete to the operator, but the
entire distributed action remains reversible. Only `finalize` closes rollback.

Finalization MUST prove:

- every expected host remains reachable;
- every participant still belongs to this action and has no foreign pending work;
- validator, RPC, and Hub/FDB states all match the prepared result;
- all guard assertions are fresh for current generations;
- every distributed rollback layer and participant frame is accounted for;
- every expected replica agrees on the pending resulting state;
- the same operation still owns every expected replica reservation;
- one full-set certificate binds the exact current head to the exact proposed
  `pending-action-finalized` successor.

The committed network-journal `pending-action-finalized` entry is the
irreversible boundary. After it commits, an opposite change is a new `add-node`
or `remove-node` action with new prestates and a new rollback stack.

### Repair-only route reconciliation

`rpc-propagate` or another explicit route-repair command MAY reconcile a damaged
route graph using the same typed prestate and rollback rules. It is not part of
the normal success path and MUST NOT be required after a successful node
action.

## Diagnosis report

A Mother diagnosis report is read-only and SHOULD contain at least:

```json
{
  "schema": "mother.diagnosis.v1",
  "network": "mainnet",
  "generated_at": "iso-8601",
  "current_operation": {
    "operation_id": "reseal-mainnet-001",
    "kind": "reseal-qbft",
    "stage": "do-complete-pending-finalize",
    "scopes": ["network:mainnet"],
    "allowed_next_commands": [
      "mother reseal-qbft finalize mainnet",
      "mother rollback mainnet"
    ]
  },
  "services": [
    {
      "service_name": "mainneta-super1",
      "host": "coolify-a",
      "coolify_uuid": "...",
      "exists": true,
      "coolify_state": "running:healthy",
      "guard_url": "http://10.116.0.3:41600",
      "guard_reachable": true,
      "validator_address": "0xb5...",
      "validator_rpc": {
        "running": true,
        "json_rpc_ok": true,
        "block_number": 6164,
        "peer_count": 1
      },
      "qbft_validators": ["0xb5...", "0x6c..."],
      "lifecycle_markers": {
        "admission": {"status": "not-required-first-node"},
        "removal_handoff": {"status": "ready", "stale": true},
        "reseal": {"status": "none"}
      }
    }
  ],
  "classification": {
    "status": "drifted",
    "reasons": []
  }
}
```

Diagnosis MUST NOT decide to fix anything. It only reports facts, topology
classification, current operation ID, current stage, rollback availability, and
active operation constraints. The diagnosis report is the normal way for an
operator to learn which operation Mother currently owns and which finalize or
rollback command is allowed next.

## Operation file

A prepared Mother operation file SHOULD contain at least:

```json
{
  "schema": "mother.operation.v1",
  "operation_id": "reseal-mainnet-001",
  "kind": "reseal-qbft",
  "stage": "prepared",
  "network": "mainnet",
  "current": true,
  "idempotency_key": "...",
  "created_at": "iso-8601",
  "created_by": "operator",
  "current_pointers": [
    "/runtime/state/mother/current/mainnet.json",
    "/runtime/state/mother/current/scopes/network_mainnet.json",
    "/runtime/state/mother/current/scopes/service_mainneta-super1.json",
    "/runtime/state/mother/current/scopes/service_mainnetc-super1.json"
  ],
  "scopes": [
    "network:mainnet",
    "service:mainneta-super1",
    "service:mainnetc-super1"
  ],
  "operator_intent": {
    "nodes": ["mainneta-super1", "mainnetc-super1"]
  },
  "observed_before": {},
  "desired_state": {},
  "preconditions": [],
  "mutation_steps": [],
  "rollback_stack": [],
  "postconditions": [],
  "checkpoints": [],
  "allowed_next_commands": [
    "mother reseal-qbft do mainnet",
    "mother rollback mainnet"
  ]
}
```

The operation file is immutable with respect to intent and desired state. Its
runtime status, checkpoint list, provisional-frame view, and `rollback_stack`
field are derived projections rebuilt from the committed action and rollback
journals. `do` MUST NOT edit the desired state it was asked to perform.

Mother MUST treat the replayed action journal, rollback journal, and referenced
participant receipts as the source of truth for rollback. `rollback_stack` is an
inspectable LIFO projection of promoted frames, not an independently mutable
authority. A local control script MUST NOT ask the operator for rollback details.
If a local action needs a backup file, previous config, old route, service UUID,
or validator address in order to undo itself, that data MUST be durably captured
before the forward step is considered complete.

## Safety rules

Mother safety rules:

- Every authoritative mutating command has `prep`, `do`, and `finalize` stages.
- `repair-projections` is the sole documented one-shot exception because it is
  non-authoritative, local-only, atomic, idempotent maintenance fenced by an
  unchanged authoritative local head.
- Every prepared operation accepts `rollback` until its documented irreversible commit point.
- An established-network writer MUST obtain one current durable reservation receipt
  from every exact expected replica before any live mutation or authoritative
  network-journal successor is allowed.
- Partial or split reservations authorize nothing, never expire automatically,
  and MUST NOT cause Mother to select a winner.
- Every authoritative network-journal transition MUST carry a full-set certificate
  for its exact predecessor and exact immutable successor.
- The distributed reservation lives outside the ordinary rollback stack and
  remains held through remediation, rollback, and
  `finalized-replication-pending`.
- Mother stores the active operation ID as the current operation for every owned scope.
- `mother diagnose` MUST report the current operation ID and allowed next commands.
- Rollback defaults to the current operation; the operator MUST NOT have to describe what to undo.
- Mother owns the provisional frame, promoted rollback stack, and reverse
  execution order.
- A destructive forward step MUST NOT run before its provisional rollback frame
  is durable.
- A frame MUST NOT enter the active rollback stack until the forward poststate is
  freshly verified and promotion is durably committed.
- A failed or ambiguous step remains provisional and enters
  `remediation-required`.
- Partial rollback MAY pop only a contiguous number of promoted top layers, after
  resolving the provisional layer first.
- Every mutating operation declares affected scopes during `prep`.
- A scope MUST NOT have more than one active non-finalized operation.
- A conflicting command MUST be rejected until the active operation is finalized
  or rolled back.
- `prep` is the only stage that interprets operator intent.
- `do` performs only the prepared operation.
- `finalize` proves completion; durable scope ownership is released only after
  the exact finalization head is acknowledged by every expected replica.
- `rollback` backs out a non-finalized operation or honestly reports why it
  cannot.
- Diagnosis and sealed-state preflight are always read-only.
- `sync-state` MUST preserve authority and lineage, own the exclusive
  local-adoption scope, durably persist the complete candidate and
  `activation-prepared` record, and commit only through its atomic durable
  active-generation pointer switch after final revalidation.
- `repair-projections` MUST NOT run while the local-adoption scope is owned. It
  MUST pin and recheck the exact authoritative local head before atomically
  publishing one complete replay-derived projection generation through one
  durable pointer switch. Head-change retry MUST be bounded.
- Service count is never validator count.
- Coolify service existence is never proof of QBFT membership.
- QBFT membership is never proof that a Coolify service exists.
- Reseal is not a deployment operation.
- Commands other than `add-node` and explicit `restore-service` MUST NOT create
  or recreate a Coolify super-node service.
- `remove-node` MUST NOT delete a Coolify super-node service outside its
  explicitly prepared target phase or before its Hub/FDB, RPC, and validator
  dependencies have been removed and freshly verified.
- Add/remove validator phases use the frozen guard-mediated QBFT proposal and
  participant manifest or explicit hard topology mode; they are not drift repair
  operations.
- A submitted or accepted validator vote is intermediate evidence only; the
  membership layer succeeds only after complete desired-set agreement and
  post-membership block progress are freshly proven.
- A command MUST NOT silently switch operation kind.
- A command MUST NOT silently widen scope.
- A command MUST NOT hide mutation inside a verifier.
- A command MUST NOT call a destructive helper from another lifecycle path.
- Add-node MUST establish internal service readiness before requesting validator
  admission.
- Add-node MUST NOT publish RPC routing before validator admission is proven.
- Add-node MUST NOT publish Hub/FDB topology before RPC reconciliation succeeds.
- Remove-node MUST withdraw Hub/FDB and RPC dependencies before validator
  removal and service deletion.
- Soft/hard topology mode is chosen during `prep` and MUST NOT change during `do`.
- Mother MUST distinguish observed topology, finalized topology, and replicated pending distributed state.
- Unknown or unsupported required schemas/capabilities permit diagnosis and export
  but block mutation, rollback, finalize, reseal, migration, and replacement-head
  activation until compatibility is proven.
- Replacement-head recovery requires exact full-set replica agreement and restores
  the complete private state, journals, checkpoints, pending action, rollback
  rights, and head-authority metadata before activation.
- Mother and guard mutation APIs MUST NOT be exposed through public Traefik routes.
- Remote operator access to local-only Mother APIs MUST use a Coolify/Allfather
  mediated call-runner or another explicitly trusted bootstrap transport.
- A call-runner is reusable transport and never operation authority. Killing,
  restarting, quarantining, or explicitly replacing it MUST NOT corrupt Mother
  state or be required to roll back a distributed node operation.
- A crashed, timed-out, or ambiguous runner is retained for inspection and is
  not automatically deleted; the same host runner is reused after reconciliation.

## Minimum implementation sequence

Mother SHOULD be implemented in this order:

1. Mother durable-state bootstrap
   - create `/runtime/state/mother/`;
   - create `/runtime/state/mother/identity.private.yaml`;
   - reserve network identity, officer/admin identities, node validator keys,
     validator addresses, first-genesis material, and route reservations;
   - create durable locations for actions, rollback stacks, routes, guards,
     locks, successor reservations, successor certificates, topology, and
     version/capability records;
   - store the initial private identity backend as inline local private YAML;
   - make any `*_key_ref` values internal references to records in the same
     private-state document.

2. Mother control API shell
   - mounts `/runtime/state/mother/`;
   - reports version, explicit readable/writable schemas, executable capabilities,
     state root, active operations, checkpoints, rollback stacks, reservation
     distributions, and successor certificates;
   - treats the container and mounted API implementation as replaceable;
   - freezes action compatibility requirements and refuses authoritative mutation
     when any required schema or capability is unknown or unsupported.

3. Coolify-mediated local call-runner transport
   - keeps Mother and guard APIs local/private only;
   - creates at most one ordinary stable reusable runner service per Coolify host;
   - limits each runner to one active request by default;
   - reuses the same service after successful requests and reconciled crashes;
   - never automatically deletes a crashed, timed-out, or ambiguous runner;
   - makes manual runner kill/restart/quarantine/delete safe because authoritative
     request and operation state remains under `/runtime/state/mother/`;
   - creates a replacement only after explicit quarantine or removal of the
     existing host runner;
   - uses structured local-call envelopes instead of a general remote shell.

4. `mother_diagnose.py` and `mother_probe_topology.py`
   - read-only;
   - no locks;
   - reports services, guards, validators, QBFT sets, route state, lifecycle
     markers, active operations, observed topology, finalized-topology drift,
     replicated pending action state, and the current operation ID.

5. Mother operation ledger and scope lock model
   - durable operation files;
   - current-operation pointers per network and scope;
   - active operation records;
   - conflict detection;
   - idempotency keys;
   - durable per-replica operation reservations, successor claims, receipts,
     cancellation tombstones, certificate storage, and release records;
   - generic rollback resolution.

6. Local generation and projection maintenance
   - durable active-generation pointers and an exclusive local-adoption scope;
   - the complete normative `sync-state` transition and allowed-command table;
   - staged `sync-state prep/do/rollback/finalize`;
   - candidate verification against unanimous expected replicas;
   - immutable, fully flushed candidate generations and durable
     `activation-prepared` records before pointer activation;
   - pointer-deterministic crash recovery and terminal scope release;
   - head-fenced, generation-atomic `repair-projections` with bounded retry;
   - no authority or lineage changes through either maintenance path.

7. `prep`
   - creates operation files;
   - declares scopes;
   - records desired state, mutation steps, prestate-capture and restore
     contracts, and postconditions;
   - does not fabricate live rollback frames before `do` captures actual prestate.

8. Full-set writer-fencing and successor-commit engine
   - implement `MOTHER-DESIGN-026`;
   - attempt claims against the exact sealed replica set without a discovered
     anchor or automatic winner;
   - atomically persist per-replica owner and exact-successor claims;
   - assemble and revalidate full-set certificates;
   - reject all mutation under partial, split, stale, canceled, or divergent
     reservations;
   - support idempotent retry, full-set cancellation tombstones, crash
     reconstruction, and terminal release;
   - retain reservation ownership through rollback and
     `finalized-replication-pending`.

9. Stage runner
   - `do`;
   - checkpoints;
   - fail-closed transition through `reserving-successor` and
     `reservation-incomplete`;
   - full-set certification and replication of `pending-action-opened` before the
     first live mutation;
   - durable provisional-frame arming before destructive steps;
   - full poststate verification and atomic frame promotion;
   - remediation-required reports;
   - retry/resume using the existing frame.

10. Generic `rollback`
   - resolves the current operation automatically;
   - restores an unresolved provisional frame before promoted layers;
   - supports all, count, and through for a contiguous top-of-stack range;
   - uses the action/rollback journals and referenced participant receipts as
     authority; the rollback stack is a replayed operational projection;
   - available until the operation's documented irreversible commit point;
   - releases scopes only after full rollback verification, exact rolled-back
     head replication, full-set successor-reservation release, and
     current-operation pointer cleanup.

11. `finalize`
   - postcondition checks;
   - exact-successor certification of the authoritative finalization transition;
   - cross-journal finalization preparation;
   - network-journal promotion from pending desired topology to finalized
     topology;
   - closes the rollback window immediately at the authoritative network-journal
     commit;
   - enters `finalized-replication-pending` and retains scopes while any expected
     replica acknowledgement is missing;
   - retries or remediates full replica acknowledgement without reopening
     rollback;
   - enters `finalized` and releases scopes and the successor reservation only
     after every expected replica acknowledges the exact finalization head and
     confirms durable reservation release.

12. Distributed QBFT membership controller
   - frozen proposal, voter, and observer manifests;
   - guard-mediated participant vote requests and durable receipt retrieval;
   - desired validator-set and post-membership block assertions;
   - compensating membership rollback using the captured before-set;
   - no promotion from `vote-submitted` alone.

13. Distributed route and Hub/FDB resource controllers
   - typed complete-prestate capture;
   - host-local RPC desired-state reconciliation;
   - node-local Hub/FDB desired-state reconciliation;
   - distributed rollback-layer accounting and assertions.

14. `mother_add_node.py`
   - service and identity preparation;
   - initial, guard-mediated soft, and hard validator admission;
   - RPC reconciliation;
   - network-wide Hub/FDB reconciliation;
   - one rollback-capable action until the documented irreversible commit point.

15. `mother_remove_node.py`
   - network-wide Hub/FDB withdrawal;
   - RPC withdrawal;
   - guard-mediated soft and hard validator removal;
   - service detach/disable/archive/removal;
   - one rollback-capable action until the documented irreversible commit point.

16. `mother_reseal_qbft.py`
   - full-set hard topology repair for existing services;
   - in-place guard-mediated config repair only;
   - no Coolify service deletion or compose changes.

17. `mother_restore_service.py`
   - explicit service repair only;
   - no QBFT membership mutation.

18. Schema and capability registry
   - typed schema identifiers on every durable and wire object;
   - explicit read, write, verifier, restore, and controller capabilities;
   - frozen per-action compatibility requirements;
   - fail-closed preflight and explicit journaled migration only.

19. `mother_recover_head.py`
   - discovers the exact expected replica set from a recovery descriptor;
   - requires unanimous compatible replica state and one complete transitive
     recovery-object closure;
   - restores the complete local state root and private recovery bundle atomically;
   - replays journals and rebuilds all projections;
   - verifies live guard truth without silently changing recovered history;
   - activates a new replicated head epoch only after full-set acknowledgement;
   - requires every later ordinary writer to use `MOTHER-DESIGN-026`.

20. Prospective replica and zero-network bootstrap
   - implement only after `MOTHER-OPEN-017` is resolved.

21. Finalization replication-state reconciler
   - implement only after `MOTHER-OPEN-018` is resolved.

22. Requirements-language lint
   - parse Markdown prose separately from fenced examples and quotations;
   - normalize each logical prose paragraph across Markdown line wrapping before
     evaluating modal constructions, so a split phrase such as `MUST` followed by
     `never` on the next source line is still rejected;
   - reject lowercase `must`, `must not`, `should`, `should not`, and `may` when
     used as unidentified normative language;
   - reject malformed or semantically weak normative forms such as `MUST not`,
     `SHOULD not`, `MUST never`, `SHOULD never`, `MAY not`, or permissions used
     to encode prohibitions such as `MAY have only one`;
   - require mandates, recommendations, permissions, and prohibitions to use the
     correct uppercase normative keyword, including `MUST NOT` for mandatory
     prohibitions;
   - recognize explicitly labeled invariants, normative schemas,
     state-transition tables, safety-rule sections, and requirement blocks as
     normative declarative contracts;
   - keep ordinary descriptive prose non-normative and unambiguous.


## Current operating lesson

The immediate lesson from the Allfather failures is:

```text
A lifecycle command without staged ownership is a half-transaction waiting to
happen.
```

Mother MUST NOT repeat that mistake.

The Mother rule is:

```text
First Mother is told what will happen.
Then Mother prepares an immutable operation record.
Then Mother does exactly that operation.
Then Mother finalizes it, or rolls it back.
Until the documented irreversible commit point or completed rollback, Mother
refuses a different story for the same scope.
```

For validator lifecycle work, that means:

```text
reseal-qbft:
  in-place config repair, staged as prep/do/finalize, rollback until the documented irreversible commit point

add-node:
  service creation, validator admission, RPC routing, and Hub/FDB topology as
  one staged distributed action, rollback until the documented irreversible commit point

remove-node:
  Hub/FDB withdrawal, RPC withdrawal, validator removal, and service removal as
  one staged distributed action, rollback until the documented irreversible commit point

restore-service:
  explicit service repair, staged as prep/do/finalize, rollback until the documented irreversible commit point
```

The control surface SHOULD make partial operations visible, retryable, and
rollback-aware. It SHOULD NOT make the operator guess whether the system is in
the middle of a story.
