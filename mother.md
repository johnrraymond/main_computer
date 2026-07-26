# Mother control surface

Status: design baseline for the new `mother` namespace.

`mother` is the replacement control surface for validator lifecycle operations that
have outgrown `tools/allfather_control.py`. Allfather remains useful reference
material for Coolify API access, private-state loading, guard/probe mechanics,
existing network naming conventions, and the current super-node runtime model,
but it is no longer the lifecycle authority. Mother starts from clean boundaries.

The immediate purpose of Mother is to make network state observable, prepare an
explicit operation, save that operation as the current operation for the affected
scopes, perform the prepared operation exactly as written, and then finalize it
or roll it back. No Mother command may mutate live infrastructure during
discovery, and no command may borrow a destructive helper from another lifecycle
path merely because it happens to touch the same service.


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
and lock state must not live only inside the container filesystem.

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
Coolify host must be reachable, agree on the committed network journal/state,
and prove that it has no unresolved action, active rollback, provisional guard
frame, or conflicting resource lock. The action is allowed to begin only after
that full-network clean-state barrier succeeds.

At every point after `prep` and before `finalize`, `mother diagnose mainnet`
must report the current operation ID, stage, distributed participants, owned
scopes, completed checkpoints, any unresolved provisional layer, active rollback
layers, the currently pop-able contiguous stack range, and allowed next
commands. Rollback is generic:

```text
mother rollback mainnet
```

It resolves the active operation from the Mother control surface and unwinds the
distributed durable rollback stack. The operator does not have to identify an
internal validator, RPC, Hub/FDB, or service phase.

No unknown in this use case may be hidden inside an implementation. If a step
depends on behavior that is not yet designed, the document must contain an
explicit `MOTHER-OPEN-*` node before implementation begins.


## Design goals

Mother exists to answer three questions before any change is made:

1. What exists?
2. What state is it actually in?
3. What exact staged plan would move it to the desired state?

Only after those answers are recorded should a mutating script act.

Mother must make the following facts distinct at all times:

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
  network/service/validator scope, which stage it has reached, and whether it may
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

The state root is created before the Mother control surface is deployed. It is
the durable source for identities, topology records, action journals, active
rollback stacks, immutable rollback journals, route before-state snapshots,
guard observations, locks, sealed complete network-state records, and network
facts that must survive replacement of the Mother container or API
implementation.

The local state root is the active head copy while the operator is running a
Mother control command. Participating machines also keep sealed replica copies of the complete
network state, including any pending distributed action, for crash recovery. Remote replicas may be stale or
newer than the local head copy; the control script must run the sealed-state
preflight before it trusts either side.

The Mother container is disposable. Pushing a new Mother compose or replacing the
mounted Mother API code must not destroy authoritative state. On startup, Mother
must rehydrate its view from `/runtime/state/mother/` plus live guard/topology
discovery.

`identity.private.yaml` plays a similar role to the Allfather private file, but
with stricter ownership boundaries:

- it is owned by Mother, not by ad hoc lifecycle scripts;
- it is topology-aware;
- it records reserved node identities before nodes are deployed;
- it stores private key material with restrictive permissions;
- it is not a substitute for the operation ledger;
- it must not be rewritten opportunistically by probes.

The private identity file should be readable and writable only by the Mother
control surface. Recommended local permission target is equivalent to `0600` on
Unix-like systems. If the file is copied or backed up, that copy is also private
state.

Minimum conceptual contents:

```yaml
schema: mother.private.v1
control_surface:
  id: mother-control-001
  created_at: "..."
networks:
  mainnet:
    chain_id: 20260001
    genesis:
      source: mother-private
      first_topology_mode: initial
      qbft:
        blockperiodseconds: 2
        epochlength: 30000
      alloc_accounts:
        - ref: officer:mainnet:hub-admin
    officers:
      hub-admin:
        address: "0x..."
        private_key: "0x..."
      deployer:
        address: "0x..."
        private_key: "0x..."
    nodes:
      mainneta-super1:
        host: coolify-a
        validator:
          address: "0x..."
          private_key: "0x..."
        validator_key_ref: "networks.mainnet.nodes.mainneta-super1.validator"
        guard_route_reservation: "..."
        rpc_route_reservation: "..."
        hub_route_reservation: "..."
      mainnetc-super1:
        host: coolify-c
        validator:
          address: "0x..."
          private_key: "0x..."
        validator_key_ref: "networks.mainnet.nodes.mainnetc-super1.validator"
        guard_route_reservation: "..."
        rpc_route_reservation: "..."
        hub_route_reservation: "..."
```

The exact storage format may change, but these ownership rules may not:

1. Mother reserves validator identity before `add-node`.
2. `add-node` installs reserved identity; it does not invent validator identity.
3. `add-node` activates chain topology, RPC routing, and Hub/FDB topology using
   Mother-owned facts as ordered phases of one action.
4. The first-node genesis is generated from Mother private state, not guessed by
   a running super-node.
5. Public/officer/admin identities are generated before deployment and recorded
   in private state.
6. Operation records may refer to secrets in private state, but should not copy
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
Mother private state may use internal references such as
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
passes the private key only to the component that must sign or deploy. Node
actions may create, delete, or repair services and routes, but they must not
delete, regenerate, or rotate these private-state identity records unless the
operator explicitly requests identity rotation.

`MOTHER-DESIGN-005: route-gated-standby-runtime`

`MOTHER-OPEN-002: standby-hub-runtime-behavior` is resolved as internal-only,
route-gated standby. A standby service may keep internal guard/runtime processes
available for diagnostics and recovery, but public Traefik routes must not point
at it. Entering standby withdraws RPC and Hub public routes and records the
previous route state in the rollback stack before the route change is applied.
Leaving standby does not publish routes by itself; the enclosing `add-node` or
`remove-node` action commits routing through its typed RPC and Hub/FDB phases.

`MOTHER-DESIGN-006: api-first-guard-runtime-control`

`MOTHER-OPEN-003: guard-runtime-api` is resolved as API-first runtime control.
High-level Mother actions are decomposed into ordered calls against the Mother
control surface and per-node guard endpoints. Runtime topology changes must not
depend on replacing compose files.

Compose may provision or replace the disposable service shell, but runtime
mutations are API operations. The guard API must expose primitives that can
capture and restore complete declared prestate. Prestate restoration must be
idempotent; forward primitives should also be idempotent where practical. The
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

Before every mutating guard or routing API call, Mother must identify the full
mutation scope, capture the complete current prestate for that scope, and durably
arm a provisional rollback frame that can restore that prestate. The frame
defines a desired prior state and verification contract, not merely an inverse
command. It is promoted onto the active rollback stack only after the forward
desired state is freshly verified and the promotion event is durably committed.
Provisional frames, the active rollback stack, and the immutable rollback journal
must all be inspectable through the Mother API.

`MOTHER-DESIGN-007: disposable-mother-container-durable-state-root`

The Mother container and Mother API implementation are replaceable. Operators may
push a new Mother compose or install updated Mother API code whenever needed, but
authoritative Mother state must live under `/runtime/state/mother/`, not inside
the container filesystem. Mother startup must validate the state root, load
identity, action, rollback, route, topology, guard, lock, and version records,
then reconcile those records with live guard/topology discovery.

Rollback stacks are required for topology/runtime mutations, not for ordinary
Mother container replacement. The safety condition for replacing the Mother
container is that the new implementation understands the mounted state schema or
refuses mutating actions until an explicit migration is performed.

`MOTHER-DESIGN-008: coolify-mediated-local-call-runner-transport`

Mother and guard mutation APIs are local-only control APIs. They must bind to
localhost or a private host/container network and must not be published through
public Traefik routes. Public routes are for user-facing Hub/RPC traffic, not for
runtime mutation endpoints.

Remote operator access to Mother is mediated by Coolify/Allfather bootstrap
access. The Coolify API is used to place a small call-runner on the target
Coolify host. The runner executes a structured local HTTP call into the Mother
API, records or prints the result, and then exits or waits for another request.

Preferred transport is Option A: a one-shot temporary call-runner service per
operator call. The operator creates or updates the service through Coolify,
passes a request envelope, starts it, reads the result from logs or durable
Mother state, and deletes or lets the temporary service stop.

Accepted fallback is Option B: a persistent private call-runner service. A
persistent runner is allowed only if it is disposable. It may be stopped,
restarted, deleted, or manually killed without corrupting Mother state. It must
not own authoritative topology, identity, operation, rollback, route, or lock
state. Killing the runner may lose an in-flight transport response, but it must
not erase a Mother operation once Mother has accepted it; the operator must be
able to recover by reading Mother status, operation records, and idempotency
results from `/runtime/state/mother/`.

The runner must not be treated as a general public shell. Its normal contract is
a structured local-call envelope such as target, method, path, body, and
idempotency key. The runner may call only approved local/private Mother or guard
endpoints.

The Coolify API credential is the authorization boundary for placing and
executing that runner. Once the Coolify-authorized runner is executing on the
private host, Mother and guard endpoints do not require a second application
credential. Local host access, access to the private container network, or the
ability to launch arbitrary workloads on that host is treated as complete host
compromise and is outside Mother's threat model.

This does not permit public exposure. Mother and guard endpoints remain
local/private-only and must never receive public Traefik routes. The Coolify API
credential remains operator-side control-plane material and must not be copied
into Mother journals, rollback frames, replicated state, participant receipts,
or ordinary command output.


`MOTHER-DESIGN-009: active-local-head-with-sealed-network-replicas`

The active Mother authority is the local head node: the machine where the
operator is running the Mother control script. The local head owns operator
intent, prepares operations, drives `do`/`finalize`/`rollback`, and is the only
writer allowed to commit global network-journal transitions during that command.

Remote Coolify hosts are not independent topology authorities. They are
execution targets and sealed-state replicas. They may hold local provisional
operation state for work that affects only that host, but they must not
independently advance finalized topology or replicated pending-action state.

Every network has a sealed complete network-state record replicated to the
Coolify hosts named by that record. A durable journal transition may describe
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
pending_action_id: "add-node-mainneta-super4-001"
pending_action_phase: "rpc-routing-verified"
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
not an advisory inventory list. A host remains part of the expected replica set
until an explicit reseal commits a new replica set that excludes it.

Before any Mother command talks to or mutates a remote network, the local control
script must run a sealed-state and journal preflight:

1. load the local complete network-state document and active journal lineage;
2. replay the active journal lineage and prove that the reconstructed state,
   including finalized topology and any pending distributed action, exactly
   matches the local network-state document;
3. load the expected `replica_hosts` from that reconstructed state;
4. query every expected replica host for its journal head, active checkpoint,
   replayed state hash, finalized topology epoch, and pending-action metadata;
5. stop before normal mutation if any expected replica host is unreachable,
   cannot replay its journal, or does not return usable network-state metadata;
6. require every expected replica and the local head to agree on the active
   checkpoint, journal head sequence/hash, finalized topology epoch, pending
   action identity/phase, and complete state hash;
7. if every expected remote agrees and the local head is stale, copy down the
   agreed journal and complete network-state document, replay locally, and verify
   again before continuing;
8. if journals diverge, journal replay disagrees with a network-state document,
   a required record is missing, equal finalized epochs have different complete
   state hashes, pending-action metadata differs, or live facts contradict the
   reconstructed state, refuse normal mutation and require remediation or an
   explicit rectification/reseal operation.

Normal mutation uses full expected-replica-set agreement, not an automatic
majority quorum. For example, if `coolify-a` and `coolify-c` agree but
`coolify-b` is unreachable while the current state still lists all three hosts,
Mother must not silently proceed with two of three.

The operator has exactly two availability choices when an expected replica is
unreachable:

1. restore reachability to that host and rerun preflight; or
2. explicitly reseal the network with a new replica set that excludes the
   missing host.

Resealing without a missing host is a network-visible recovery action. It must
create a new topology epoch and state hash, record the removed host and reason,
write the new state and journal records to every remaining expected replica, and
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
definition. It must be refreshed from the current committed state and explicitly
re-included through a replica-rejoin or reseal operation that creates another
new epoch.

Wall-clock modified time may be used as an operator hint, but it is not the
authority. The authority is the replayed journal lineage, sealed epoch, state
hash, active checkpoint, and expected replica set. `modified_at` fields should
be recorded for diagnostics, but normal mutation must compare the cryptographic
and sequence metadata.

Host-local capture details, temporary files, retry logs, and transient health
samples remain in the action, rollback, and participant journals. The replicated
network journal nevertheless records the network-scoped action as soon as the
full-network barrier is crossed. Its complete state contains the last finalized
topology plus the current pending distributed action.

Every meaningful distributed transition is appended and replicated as it occurs,
including:

```text
pending action opened
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

The global entry may reference detailed participant receipts and action/rollback
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

For removal, public route withdrawal may happen early for safety. The
route-withdrawn result is therefore committed immediately as a pending-action
transition and replicated to every expected host. It does not become finalized
topology until `finalize`. Its complete prestate and restore attempts remain in
the action-specific rollback journals, referenced by the pending network state.

`reseal` is an explicit recovery operation, not a normal sync. It is used when
remote replicas disagree, an expected replica is unreachable and must be
excluded, an excluded host must be re-included, the network is wedged, a sealed
state cannot be proven, or the operator intentionally chooses a new committed
state from live facts. Reseal must inspect local and remote journals and states,
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
rollback availability and remediation status
expected replica set
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
preflight does not merely compare copied state files; each replica must open and
replay its journal through the common journal engine and report the resulting
hash.

On every command that reads or may mutate a network, Mother must:

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
blocked. The operator must select an explicit rectification path. Supported
conceptual paths are:

```text
rebuild-committed-state-from-journal
restore-journal-from-agreed-remote
select-journal-lineage
force-authoritative-checkpoint-from-live-facts
```

Mother must show the conflicting local and remote heads, reconstructed hashes,
complete network-state hashes, and relevant live facts before the operator chooses.
Rectification must never silently pick a winner.

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

The checkpoint and reconstructed state must be replicated to every host in its
declared `replica_hosts` set. Normal mutation remains blocked until every listed
replica reports the same checkpoint hash, journal head, replayed state hash, and
committed-state hash.

A forced checkpoint is allowed only through explicit rectification/reseal
workflow. Ordinary `add-node`, `remove-node`, and route reconciliation commands
must never create one automatically.

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

Names may change, but the behavior must not: replay happens before trust,
unreconcilable disagreement requires operator choice, and a forced baseline is
recorded as a new authoritative checkpoint rather than as edits to old history.


`MOTHER-DESIGN-011: prestate-first-rollback-with-rollback-journal`

`MOTHER-OPEN-005: crash-and-ambiguous-step-recovery` is resolved by treating
the complete prestate of each declared mutation scope as the unit of recovery.

Before a mutating substep starts, Mother must:

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
desired prior state. It must not rely only on an inverse verb such as
`start -> stop` or `add -> remove`, because an inverse verb may not recreate the
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
and active invariant set. Only a successful verification may commit the single
action-journal transition:

```text
step-applied-verified-and-promoted
```

That transition changes the frame from `armed-provisional` to a completed,
executable item in the active rollback-stack projection. Mother may continue to
the next forward step only after that promotion is durable. For a distributed
step, promotion occurs only when every required participant frame is armed and
every required participant has freshly verified the same desired resulting
generation.

If the forward mutation fails, is interrupted, returns an ambiguous result, or
cannot pass the required assertions, its frame remains `armed-provisional`. The
action enters `remediation-required`; the failed frame is not represented as a
completed rollback-stack item. The operator may retry/resume using the same
frame, or restore and close the provisional frame before rolling back completed
stack layers. Mother must never recapture prestate over a partial result and call
that partial result the new prestate.

Rollback remains available from successful `prep` until successful `finalize`.
`finalize` is the only operation stage that permanently closes the rollback
window. After finalization, reversing the result requires a new prepared action
with its own prestate and rollback stack.

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

An unresolved provisional frame is logically above the promoted stack. Before
Mother may pop any completed layer, it must first either:

```text
retry/resume and promote the provisional frame after successful verification
or
restore its complete prestate, verify it, journal provisional-restored-verified,
and close it without promotion
```

Finalization also preserves rollback history. It is forbidden while any
provisional frame remains unresolved. Before clearing the active stack,
`finalize` must append a `frame-close-prepared` record for every unused promoted
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
journal. A successful rollback may produce a new network-visible committed
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

The Mother API must expose all three operational records:

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

The capture step may write Mother control metadata, but it must not change the
live resource being protected. The apply step must refuse to begin unless the
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

Endpoint names may gain resource-specific subpaths, but they may not lose the
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

The guard must reject the request if the declared scope is incomplete for the
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

Before applying, the guard must re-read the target and prove that its current
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

The response may summarize the prestate, but it does not replace the durable
rollback frame and does not itself promote that frame. Mother must freshly run
the complete required guard set. Only after that verification succeeds may it
commit `step-applied-verified-and-promoted`, after which the frame appears on the
active rollback stack until rollback restores it or finalize closes it.
Repeating a capture or apply request with the same idempotency key and identical
request hash must return the same frame or result; reusing the key with different
content must fail.

Rollback uses:

```text
POST /guard/v1/prestate/restore
```

with the frame ID, expected current generation, and expected current ownership.
The restore operation applies the complete recorded prestate, verifies the
restored state hash and resource-specific postconditions, and returns
`restored-verified`. Repeating the same restore must be safe. For a promoted
frame, Mother journals every restore attempt and removes the frame from the
active stack only after a durable `restored-verified` result. For an unpromoted
provisional frame, the same verified restore closes the provisional frame
without ever adding it to the completed stack.

The guard must use structured error codes. Baseline codes are:

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

The guard must never accept arbitrary shell as a substitute for a typed mutation
contract. Resource-specific payloads remain typed, and each endpoint must define
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
booleans, cached intent, or lifecycle markers. Mother may ask a guard to verify
an assertion, but neither Mother nor another caller may set an assertion to
`true`.

The baseline assertion surface is:

```text
POST /guard/v1/assertions/verify
```

A request names the exact assertion set and scope that must be evaluated:

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

For every requested assertion, the guard must execute the assertion's versioned
verifier against the underlying resources at request time. It may inspect files,
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

A false result must identify the failed conditions and return the non-secret
evidence needed to diagnose them. Evidence must never expose private key
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

A composite result must retain the results and evidence hashes of its leaf
assertions. Mother must never receive an unexplained top-level `true`.

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

Before every forward step, Mother must freshly verify the complete union of:

```text
mandatory control-safety assertions
all currently active action invariants
all invariants the next step declares it will preserve
the next step's direct preconditions
```

This is the default and currently supported guard behavior. Mother does not
check only the immediately preceding step. If an identity, runtime, route, lock,
journal, rollback frame, or other earlier requirement drifted after it was first
established, the next step must be blocked.

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
postcondition set. Only then may it commit
`step-applied-verified-and-promoted`, add the frame to the active rollback-stack
projection, add established assertions to the active set, retire superseded
assertions, and consider the next step. A command exit code or mutation response
is not proof of the resulting truth.

Rollback uses the same assertion contract. A rollback frame declares the
assertions that prove its complete prestate has been restored. The frame remains
at the top of the active stack until those assertions are freshly true, the
verification evidence is durably appended to the rollback journal, and the
result is `restored-verified`.

Finalization, pending network-state transitions, reseal, authoritative
checkpoint creation, and finalized-topology transitions must freshly verify
their required assertion sets immediately before their journal commit points.

Implementation must keep assertion-set selection separate from assertion
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
must never be interpreted as completed action history.

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
state obtained by valid replay through `covers_through_sequence` must be equal.
A routine checkpoint summarizes valid history; it does not override it.

Every newly created journal begins with an initial-state checkpoint before any
ordinary event is committed:

```text
sequence 1: initial-state checkpoint
sequence 2: first ordinary event
sequence 3: second ordinary event
```

The initial checkpoint contains the journal kind's complete defined initial
state. For example, a newly prepared action may begin with an action-state
checkpoint containing no completed steps, no active rollback frames, and
`finalized: false`.

When opening a committed journal, Mother must:

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

Readers must never assume that replay begins at sequence `1` or that entries
older than the selected checkpoint remain in the active journal directory.
This is the compatibility boundary that permits old history to be archived or
compressed later without redesigning replay.

If Mother opens a journal that has no committed checkpoint, it must not continue
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
checkpoint defined by `MOTHER-DESIGN-010`. A routine checkpoint must equal
valid prior replay. An authoritative rectification checkpoint is
operator-approved, records the superseded lineage and evidence, and may establish
a different active state after unreconcilable history. Both are immutable
checkpoint entries and both become active only through normal head commit.

No automatic checkpoint frequency, retention threshold, archive policy, or
compression command is part of the current contract. The implementation must
support appending and discovering checkpoints now; policy for adding later
routine checkpoints may be introduced without changing the journal format or
replay algorithm.

### Atomic filesystem commit

A mutating journal writer must hold the applicable exclusive operating-system
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

A writer must never update `committed-state.json`, an active rollback stack,
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
lock file is diagnostic only and may contain the process ID, action ID, owner
identity, acquisition time, and owned scopes. File existence, age, or metadata
alone must never be treated as proof that a lock is held, and a stale-looking
lock must not be broken based only on wall-clock time.

The network mutation lock serializes updates to the network journal and all
action or rollback journals that can change that network. Remote guards and
routing controllers additionally take operating-system-backed locks for the
local resources named by a mutation scope. A guard must refuse a capture,
mutation, or restore when an incompatible resource lock is held.

`diagnose` remains read-only and does not acquire the mutation lock. A read-only
journal open must read the head before and after replay. If the head changed, it
discards the result and retries from the new stable head. Mutating commands
acquire the lock before trusting replay for a write decision and verify lock
ownership as a mandatory guard assertion before every step.

### Cross-journal transitions

Mother must not pretend that two independent `head.json` replacements are one
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

A committed entry may reference another journal only by stable identity,
sequence, entry hash, and resulting-state hash. Derived operation state may
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

Mother may then append an `action-finalized` mirror entry to the action journal
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

A later rollback chosen before the network finalization commit appends an event
identifying unused `frame-close-prepared` or `finalization-prepared` records as
abandoned by that attempt. History is preserved; old records are never
rewritten.

### Rollback and finalize ordering

The common journal commit point enforces the rollback rule.

After a guard restores a frame's complete prestate and freshly verifies the
required assertions, Mother must:

```text
append rollback-restored-verified
commit the rollback-journal head
replay/rebuild rollback-stack.json without that frame
```

If Mother crashes after the rollback-journal head commits but before the stack
projection is replaced, startup replay proves the frame is complete and rebuilds
the stack without it. If the head did not commit, the frame remains active and
the idempotent complete-prestate restore may be retried.

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

On startup, and before any mutating command continues, Mother must:

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
9. block mutation when a committed head, checkpoint, or required lineage cannot
   be proven.

Temporary files and orphan entries may be archived after diagnosis, but they
must not be promoted to committed history merely because their contents look
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

Before preparing either node action, Mother must query the union of the sealed
`replica_hosts` set, every host carrying a current network node, and the proposed
target host. Every required host must freshly prove:

```text
reachable
same complete network checkpoint/head/replayed-state hash
pending_action is absent
no unresolved Mother action
no executable rollback frame
no provisional guard frame
no conflicting local resource lock
supported action, guard, route, and journal schemas
```

The action does not begin when any expected host is unavailable or pending.
Majority agreement is insufficient. The action journal records the exact
participant set, replica facts, and assertion evidence used to cross the barrier.

### Distributed rollback layers

One logical action may contain rollback layers whose participant frames are
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

After the forward distributed mutation, every required participant must freshly
verify the intended poststate and generation. Only then may Mother commit one
`step-applied-verified-and-promoted` transition for the logical layer and add it
to the completed active rollback-stack projection.

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
  "expected_chain_id": 42424240
}
```

The listed backends replace the complete Mother-owned backend set for that host
and route kind. The controller must preserve unrelated Coolify, operator, and
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

A node in private standby must also be provably absent from every Mother-owned
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
the next phase may begin.

The ordered mutation sequence is:

1. Cross the full-network clean-state barrier and acquire the network lock.
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

1. Cross the full-network clean-state barrier and acquire the network lock.
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

`rpc-propagate` may remain as an explicit repair/reconciliation command, but
normal add/remove correctness must never depend on the operator remembering to
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
`step-applied-verified-and-promoted`. The next forward layer may not begin before
that commit.

When a step fails, is interrupted, or cannot be fully verified, the action enters:

```text
remediation-required
```

The unresolved provisional layer remains logically above the completed active
stack. It is inspectable but is not reported as a completed or pop-able stack
item. Mother must display:

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

Before either full or partial rollback can pop a completed layer, Mother must
first resolve the unpromoted failed layer. It restores that layer's complete
prestate, freshly verifies the restoration on every required participant,
commits `provisional-restored-verified`, and closes the provisional frame without
promoting it. It then processes the requested number of completed top layers.
A partial rollback leaves the action open in `remediation-required` with a
recomputed active invariant set and a newly reported safe resume point.

Retry/resume reuses the existing armed provisional frame. Mother must not capture
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

The command surface may expose these choices as:

```text
mother <kind> do <network>                         # retry/resume
mother rollback <network> --all
mother rollback <network> --count <n>
mother rollback <network> --through <layer-id>
```

`--count` and `--through` operate only on a contiguous prefix from the current
top of the completed stack. The unresolved provisional layer is restored first
when present. The interactive report may offer numbered choices, but those
choices map to the same journaled operations.

A provisional frame closes only through one of these durable outcomes:

```text
applied-verified-and-promoted
provisional-restored-verified
closed-by-authoritative-rectification
```

Temporary working copies may be cleaned only after the closing event commits.
Finalization is forbidden while any provisional frame remains unresolved.
Conflicting mutations on the network or overlapping scopes remain blocked until
the action is finalized, fully rolled back, or explicitly rectified.



`MOTHER-DESIGN-017: replicated-pending-network-state-until-finalize`

The pending-versus-finalized network-state question is resolved by storing both
in the replicated network journal and complete network-state document.

At most one network-scoped pending action may exist for a network because the
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
first network-scoped mutation. Every expected replica must acknowledge the new
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

The term `committed` must therefore be interpreted precisely:

```text
committed journal transition:
  durably part of the active journal lineage

finalized topology:
  operator-accepted topology produced by pending-action-finalized
```

A pending validator-set, RPC, or Hub/FDB change may be committed to the journal
and replicated while still reversible. It must never be presented as finalized
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
`POST /qbft/propose-validator` behavior may be adapted behind the typed Mother
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

Mother does not silently shrink either set after `prep`. QBFT may enact the
change before every requested vote is submitted, but every required voter must
remain reachable and produce a durable vote receipt, and every required observer
must produce durable observation evidence for the same effective validator set
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

For a frozen voter, the guard must:

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

A compatibility adapter may map the current guard statuses
`already-admitted`/`already-removed` to `already-desired` and
`admitted`/`removed` to `desired-observed`.

`vote-submitted` is intermediate evidence only. It never proves membership
success.

Repeating the same `attempt_id` and identical request hash returns the same
durable submission receipt. A retry after a failed or inconclusive attempt uses
a new attempt ID under the same proposal and the same armed provisional frame.
Before submitting another vote, the guard must first check whether the desired
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
restores the captured zero-node/bootstrap prestate. Mother must not switch among
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

The Coolify API credential itself must not be placed in durable Mother state,
network journals, action journals, rollback journals, checkpoints, guard
receipts, or replicated private state. Existing operator-side Coolify secret and
environment-loading conventions remain the source for that credential.

If Mother or guard endpoints are ever exposed beyond the trusted local/private
host boundary, this threat model must be reopened before that exposure is
allowed. Under the current local-only architecture,
`MOTHER-OPEN-013: local-control-api-authorization` is resolved.

`MOTHER-DESIGN-020: governance-office-truth-through-standard-guard-assertions`

`MOTHER-OPEN-014: governance-office-deployment-invariant` is resolved through
the standard evidence-backed assertion path defined by `MOTHER-DESIGN-013`.
Governance identity does not introduce a second truth or verification system.

Mother must keep the expected and observed facts distinct:

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

The assertion's versioned verifier must:

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
    "chain_id": 42424240,
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

A false or unavailable result must identify which expected and observed facts
were obtainable, but it must never expose private keys. Mother must not
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
depend on governance authority. Whenever it is required, it must be freshly
evaluated immediately before the dependent journal commit. A prior result is
stale when its private-identity dependency, finalized network-state binding, or
relevant on-chain observation changes.

The exact ABI method names and contract adapters are implementation acceptance
criteria for the versioned verifier registry. An unknown contract schema,
unsupported reader, or unverifiable finality rule permits diagnosis but blocks
mutation. This is the same fail-closed compatibility behavior used by every
other guard assertion; it is not a remaining architectural question.

### Remaining open design nodes

The sealed-state format, crash/ambiguous-step recovery model, typed guard
prestate contract, evidence-backed full-guard assertion contract, durable
filesystem journal/locking model, integrated distributed route/topology
lifecycle, provisional-frame remediation lifecycle, replicated
pending-versus-finalized network-state model, guard-mediated reversible QBFT
membership contract, Coolify-API-key local-control boundary, and
evidence-backed governance-office verifier are resolved above. The following
implementation contracts remain open and must be resolved before code depends
on them:

- `MOTHER-OPEN-007: call-runner-acceptance-and-result-contract`
- `MOTHER-OPEN-011: state-schema-and-capability-negotiation`
- `MOTHER-OPEN-012: replicated-private-state-policy`
- `MOTHER-OPEN-015: replacement-local-head-recovery-procedure`


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
    state_sync.py
    operations.py
    journal.py
    atomic_files.py
    checkpoints.py
    locks.py
    planning.py
    reporting.py
    rollback_stack.py
    rollback_journal.py
```

Recommended command shape:

```text
# Read-only. Before the command trusts live network facts, the control script
# verifies local sealed state against remote replicas and refreshes local state
# when remotes agree that local is stale.
python tools/mother/mother.py diagnose mainnet

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

Names may change, but the stage contract must not change: every mutating Mother
operation is run as `prep`, `do`, and `finalize`; every prepared, unfinalized
operation accepts the generic `rollback` command until it has been finalized.

`--standby` is not a normal-path flag. Standby is the default state produced by
`add-node`.


## Relationship to Allfather

Mother may reuse Allfather as reference material, not as a shared lifecycle
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

- no Mother mutating command may call Allfather `add-node`, `remove-node`,
  removal handoff, admission, service rebuild, or compose synchronization helpers;
- no Mother reseal path may depend on image tags, compose replacement, service
  deletion, or service recreation;
- no Mother command may reuse a helper whose name, error messages, or safety
  model belongs to another lifecycle operation;
- no Mother command may hide a mutation inside a function named as a verifier,
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
validator set. It must mount `/runtime/state/mother/` and reconstruct its control
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
- deleting or recreating services except inside an explicit `restore-service`
  operation;
- treating service count as consensus truth.

### Persistent operation ledger

Mother must keep a durable operation ledger using the common filesystem journal
engine under the Mother-owned state root. A future storage-backend migration is
allowed only through an explicit migration that preserves journal identity,
checkpoint, hash-chain, atomic-head, locking, and replay semantics; no current
command may silently substitute a different authority.

Suggested durable state layout:

```text
/runtime/state/mother/
  identity.private.yaml
  topology.yaml
  version.json
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
      prestate/
        <frame-id>.json
      summary.json
  current/
    <network>.json
    scopes/
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

For each owned scope, Mother must also maintain a durable current-operation
pointer. The pointer is a replayable projection used for fast lookup and
operator visibility; the authoritative ownership decision comes from the
committed action journal together with the currently held operating-system
lock. If the pointer disagrees with journal replay, Mother must rebuild the
pointer or block when the journal itself cannot be proven.

At minimum, Mother should maintain:

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

Each current-operation pointer must include:

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

The normal operator should not need to remember the operation ID in order to
roll back. `mother diagnose` must report the current operation ID and the
allowed next commands, but `mother rollback --network mainnet` must resolve the
active operation from the current-operation pointer. Supplying an explicit
operation ID is allowed only as a safety cross-check; it must not let the
operator roll back a different operation than the one currently owning the
scope.

There may be many completed historical operations, but there may be only one
current non-finalized operation for a scope. A command with a different intent
must not create a second current operation. It must be rejected until the current
operation is finalized or rolled back.

### Control APIs

The control container should expose a local/operator-only API or CLI surface with
the following conceptual operations:

```text
GET  /v1/status
GET  /v1/version
GET  /v1/state-root
GET  /v1/diagnose/<network>
GET  /v1/networks/<network>/seal
GET  /v1/networks/<network>/replicas
POST /v1/networks/<network>/sync-preflight
POST /v1/networks/<network>/reseal/prep
POST /v1/networks/<network>/reseal/do
POST /v1/networks/<network>/reseal/finalize
GET  /v1/networks/<network>/current-operation
GET  /v1/scopes/<scope>/current-operation
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
GET  /v1/operations/<operation-id>/remediation
GET  /v1/operations/<operation-id>/provisional-frames
GET  /v1/operations/<operation-id>/rollback-stack
GET  /v1/operations/<operation-id>/rollback-stack/<frame-id>
GET  /v1/operations/<operation-id>/rollback-journal
GET  /v1/guards/<node>/topology-state
```

The HTTP shape is optional; the stage semantics, state-root visibility, current
operation visibility, sealed-state visibility, replica visibility, preflight
visibility, reseal visibility, and rollback-stack visibility are not optional.

### Remote access through Coolify call-runners

The Mother API is the control surface, but it is not a public internet API. A
remote operator reaches it by asking the existing Coolify/Allfather bootstrap
channel to start a small local call-runner on the target host.

The preferred mode is a one-shot runner:

```text
operator
  -> Coolify API
  -> create/update/start temporary call-runner service
  -> runner calls http://mother-control:<port>/v1/... or http://127.0.0.1:<port>/v1/...
  -> runner writes stdout/log result and, when available, a durable result record
  -> runner exits and may be deleted
```

The accepted fallback is a persistent private runner:

```text
operator
  -> Coolify API
  -> update request envelope for mother-call-runner
  -> restart or signal runner
  -> runner performs one local Mother/guard API call
  -> runner records result
```

A persistent runner is convenience transport only. It is safe to manually stop,
kill, recreate, or remove it. It must not hold authoritative Mother state, active
operation state, rollback frames, locks, identity material, or route snapshots.
Those records live under `/runtime/state/mother/` and inside the Mother API
state model. If the runner is killed after Mother accepts a request, the
operation remains recoverable through Mother's idempotency key, current-operation
pointer, operation record, checkpoints, and rollback stack. If the runner is
killed before Mother accepts the request, no Mother mutation has occurred.

The call-runner request must be structured. It should not expose arbitrary shell
as the normal operator interface. A baseline request envelope is:

```json
{
  "request_id": "call-...",
  "target": "mother",
  "method": "POST",
  "path": "/v1/operations/rpc-propagate/prep",
  "idempotency_key": "idem-...",
  "body": {
    "network": "mainnet"
  }
}
```

The runner must restrict `target` to approved local/private services, restrict
paths to Mother or guard API prefixes, and write enough result metadata for the
operator to distinguish transport failure from a Mother API rejection.

All mutation requests must include an idempotency key. Repeating the same request
with the same idempotency key must return the same operation record or continue
the same operation. Repeating a request with a different intent for an occupied
scope must fail with a conflict.

### Mother API implementation updates

Updating Mother code is not a topology operation. Operators may replace the
Mother compose, restart the Mother container, or install a new mounted API
implementation without creating a topology rollback stack, provided no live
topology/runtime mutation is being requested by that update.

The update safety rule is state externality:

```text
Container/code may change.
Authoritative Mother state remains under /runtime/state/mother/.
```

After every start, the Mother API must:

- report its implementation version and supported state schemas;
- report the mounted durable state root;
- validate that it can read the current identity, operation, rollback, route,
  topology, guard, lock, and version records;
- refuse mutating actions if it cannot understand the mounted state schema;
- keep read-only status/diagnose endpoints available when possible so the
  operator can see why mutation is refused.


## Three-stage mutation contract

Every mutating Mother command must be run as a set of three commands:

```text
prep
do
finalize
```

Until `finalize` has succeeded, the operation must also accept:

```text
rollback
```

This is the most important Mother boundary.

### `prep`

`prep` is the only stage that interprets operator intent.

`prep` must:

- run read-only discovery;
- classify the current state;
- validate that the requested operation is coherent;
- calculate the exact desired target state;
- calculate the exact mutation steps;
- declare the complete mutation scope, prestate capture method, restore
  operation, and rollback verification contract for every step that may be
  performed in `do`;
- acquire logical ownership of every affected scope;
- write an immutable prepared operation record;
- print the plan, risks, affected scopes, required confirmations, and rollback
  behavior.

`prep` must not:

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

`do` must:

- load the prepared operation by operation ID;
- confirm the operation is still active for its declared scopes;
- refuse if the live state has drifted beyond the prepared preconditions unless
  the prepared operation explicitly declares that drift acceptable;
- perform only the mutation steps recorded in the prepared operation;
- perform runtime mutations through Mother, guard, and routing APIs instead of
  hidden compose replacement;
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

`do` must not:

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
- promote a frame while its forward step remains failed or unverified.

If a step fails during `do`, Mother must leave its frame provisional, enter
`remediation-required`, and report the unresolved step, participant evidence,
completed rollback layers, pop-able range, and these remediation choices:

```text
mother <kind> do <network>                         # retry/resume existing frame
mother rollback <network> --all
mother rollback <network> --count <n>
mother rollback <network> --through <layer-id>
```

Retry/resume uses the same provisional frame. If the desired poststate already
holds, Mother may freshly verify and promote it. If the state is a recognized
partial result, Mother may retry the prepared mutation. If neither the prestate
nor a recognized partial/desired state can be proven, retry must be refused.

### `finalize`

`finalize` proves that the prepared operation reached its declared final state.

`finalize` must:

- run the operation's postcondition checks;
- verify that all mutation checkpoints are complete;
- verify that no armed provisional frame remains unresolved;
- verify that the desired state matches the actual state;
- append a `frame-close-prepared` record for every promoted active rollback frame
  to the immutable rollback journal;
- verify those rollback-journal records are durable;
- commit `finalization-prepared` in the action journal with exact rollback and
  pending-network-state references;
- commit `pending-action-finalized` in the network journal, promoting the pending
  desired topology to finalized topology and clearing the pending action;
- replicate and verify that network-journal head on every expected replica;
- append the action-journal `action-finalized` mirror;
- clear the active rollback-stack projection only after the network finalization
  commit;
- mark the operation complete;
- release all active scope ownership;
- make `rollback` unavailable for this operation, except as a new explicit
  recovery operation.

`finalize` must not perform hidden repair. If postconditions fail, `finalize`
must leave the operation open and report the allowed next commands:

```text
mother <kind> do <network>                         # retry/resume
mother rollback <network> --all
mother rollback <network> --count <n>
mother rollback <network> --through <layer-id>
```

### `rollback`

`rollback` is valid for every prepared operation until `finalize` succeeds.

Mother must first inspect both:

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

`rollback` must:

- resolve the current active operation from Mother's current-operation pointer
  when the operator does not provide an operation ID;
- prove that any supplied operation ID matches the current operation for the
  requested scope;
- load the same prepared operation record and acquire its required locks;
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
  promoted frame remain and the requested mode was `--all`;
- otherwise leave the operation open in `remediation-required`.

Rollback is not an operator-authored command list. Mother owns the rollback plan,
complete recorded prestates, provisional frame, promoted stack ordering,
participant membership, immutable rollback journal, and verification of every
restore. The operator chooses how far to unwind, not arbitrary low-level undo
commands.

`rollback` must be conservative. If it cannot safely undo a layer, it must say
why, preserve the layer, and leave a clear remediation report. It must not
pretend that a partial rollback is clean.

After `finalize`, rollback is no longer a stage of the completed operation.
Unused promoted frames have durable `frame-close-prepared` records referenced by
the committed network `pending-action-finalized` entry and are no longer
executable. Changing the result of a finalized operation requires a new `prep`.

#### Rollback action stack and rollback journal

Every mutation step that can affect live state must declare its complete mutation
scope and have a corresponding frame durably armed as provisional before the
mutation is allowed to execute. The provisional frame set, promoted active stack,
and rollback journal must all be inspectable through the Mother API.

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

Mother must never begin a forward action without the provisional frame. It must
also never continue to the next forward action until the current frame is
verified and promoted.

Rollback must peek rather than pop. The top promoted frame remains active while
Mother restores and verifies its prestate. Only after exact restoration is
`restored-verified` and durably journaled may Mother remove that frame from the
active stack. A provisional frame is similarly retained until its prestate is
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
cross-journal references. After that network finalization commit, no frame from
the action may be executed.

Rollback frames, promotion events, and restore attempts are durable Mother
state. They must not live only in a local shell script, terminal output,
transport response, or transient container memory. The action-specific rollback
journal is append-only and separate from the global network-state journal.


## Active operation conflict rule

Mother is told what is going to happen during `prep`. Until that operation is
finalized or rolled back, Mother must treat that prepared instruction as the
active truth for its declared scopes.

A scope may be:

```text
network:mainnet
host:coolify-a
service:mainneta-super1
validator:0xb5...
coolify-service-uuid:<uuid>
```

Every mutating operation declares the scopes it owns. While any of those scopes
has an active non-finalized operation, Mother must reject conflicting commands.

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

Then these must be rejected:

```text
mother remove-node prep mainnet --node mainneta-super1 --mode soft
mother add-node prep mainnet --node mainnetd-super1 --host coolify-d --mode soft
mother reseal-qbft prep mainnet --nodes mainnetc-super1
mother restore-service prep mainnet --node mainneta-super1
```

The error must say the next allowed commands:

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
Mother may return the existing operation. If the user repeats the same operation
without the idempotency key, Mother should still detect that an equivalent active
operation exists and ask the operator to use the existing operation ID.

The rule is:

```text
No second story starts until the first story has been finalized or rolled back.
```

If Mother is told a different story while a current operation exists for an
overlapping scope, Mother must not reinterpret the new command as a correction.
It must answer with the current operation ID, its stage, and the exact allowed
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
```

The operator should not need to pass `--operation-id` for these commands because
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

A topology probe must not mutate service definitions, deploy new probes by
rewriting existing services, restart containers, submit validator votes, write
genesis files, clear markers, enable routes, disable routes, delete services, or
infer intent. It only observes and classifies.

### What topology probing detects

A Mother topology probe must detect at least these facts for every relevant
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

A topology probe must classify each service into explicit states. Recommended
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
allowed and which operations must be refused until rollback, finalize, reseal, or
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
None may silently overwrite another.

Before `prep` or any mutating command trusts network facts, Mother runs the
sealed-state preflight. If the remote replicas agree and the local head is stale,
Mother refreshes the local state root from the agreed sealed replica. If remote
replicas disagree, omit a pending action, or the state is wedged, normal mutation
is refused until remediation or `reseal-state` creates a new explicit seal.

`prep` compares observed topology with finalized topology and records a planned
transition. After the clean-state barrier, opening the distributed action creates
and replicates `pending_action`. `do` executes only that planned transition and
commits every meaningful phase change to the pending state. `finalize` proves
the observed topology reached the pending desired state and commits
`pending-action-finalized`, which advances finalized topology and clears the
pending action. `rollback` journals the reverse progress and, after full verified
restoration, clears the pending action without advancing finalized topology.

Until `finalize` runs, finalized topology must not pretend the operation is
complete, but the replicated network state must still describe what is physically
applied and reversible. If another command is requested for an overlapping
scope, Mother must reject it and print the active operation plus the allowed
`finalize`, retry/resume, or rollback commands.

### Topology change commands and modes

Validator membership, RPC routing, Hub/FDB topology, and service lifecycle are
phases of `add-node` and `remove-node`.

- `add-node` creates or repairs the service, installs its reserved identity,
  admits the validator, publishes RPC routing, publishes Hub/FDB topology, and
  leaves the complete distributed action rollback-capable until finalize.
- `remove-node` withdraws Hub/FDB topology and RPC routing, removes validator
  membership, detaches/removes the service, and leaves the complete distributed
  action rollback-capable until finalize.
- `reseal-qbft` repairs the entire selected QBFT topology in place and is not an
  ordinary node lifecycle command.

Mother supports three topology-change modes. The operator's primary node command
must choose or imply the mode during `prep`; `do` must not switch modes.

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
topology phase is not service deployment. It may stop and restart validator
subprocesses, but it must not delete/recreate unrelated Coolify services, rebuild
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

Mother must distinguish:

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
doing
remediation-required
do-complete-pending-finalize
finalizing
finalize-failed
finalized
rolling-back
rollback-failed
rolled-back
```

Only these states accept mutating or remediation commands:

```text
prepared:
  do, rollback --all

doing:
  retry/resume after interruption, rollback when a durable provisional or
  promoted frame makes recovery safe

remediation-required:
  retry/resume
  rollback --all
  rollback --count <n>
  rollback --through <layer-id>

do-complete-pending-finalize:
  finalize, rollback --all, rollback --count <n>, rollback --through <layer-id>

finalize-failed:
  finalize retry
  retry/resume when an unverified mutation is identified
  rollback choices

rollback-failed:
  rollback retry, inspection, explicit rectification

finalized:
  no further stage commands; create a new operation for further changes

rolled-back:
  no further stage commands; create a new operation for further changes
```

A failed or unverified current step always maps to `remediation-required`; its
frame remains provisional until it is successfully verified and promoted or its
prestate is restored and the frame is closed. Read-only diagnosis is always
allowed and must show the provisional layer, promoted stack, participant
evidence, pop-able range, and exact allowed commands.

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

`mother_plan.py` may be used internally by `prep`, but it must not mutate live
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

`prep` for reseal-state must capture:

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
- keep the entire action rollback-capable until finalize.

Stage contract:

```text
mother add-node prep mainnet --node <service> --host <host> --mode initial|soft|hard
mother add-node do mainnet
mother add-node finalize mainnet
mother rollback mainnet
```

`prep` must run the full-network clean-state barrier and record:

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

`finalize` must freshly prove:

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
- keep the entire action rollback-capable until finalize.

Stage contract:

```text
mother remove-node prep mainnet --node <service> --mode soft|hard
mother remove-node do mainnet
mother remove-node finalize mainnet
mother rollback mainnet
```

`prep` must run the full-network clean-state barrier and record:

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
- hiding a hard reseal inside an ordinary soft remove;
- beginning while any expected host has unresolved work;
- treating partial distributed completion as success.

`finalize` must freshly prove:

- target is absent from the effective validator set;
- survivors agree and block production progresses;
- target is absent from every RPC route graph;
- every node reports the desired Hub/FDB topology;
- target service state matches the prepared removal policy;
- every distributed rollback frame is present and consistent;
- all expected replicas agree on the resulting network state.

### Compatibility aliases

Mother may expose `add-validator` and `remove-validator` only as aliases that
invoke the same complete distributed `add-node` or `remove-node` action engine.
They must not expose a partial validator-only workflow when service, RPC, or
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
preflight. This preflight is local/state synchronization, not live infrastructure
mutation.

Preflight must collect each reachable replica's committed-state metadata:

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
  remotes agree on a newer epoch/hash than local.
  Mother copies the sealed state down from the network and updates local state
  before continuing.

network-replica-mismatch:
  remotes disagree by epoch or hash.
  Normal mutation is refused.

wedged:
  a seal is missing, equal epochs have different hashes, required operation
  records are missing, live guard/route facts contradict the seal, or the state
  cannot be proven.
  Normal mutation is refused.
```

Only `local-current` and `local-stale-network-agrees` may continue into ordinary
commands. `network-replica-mismatch` and `wedged` require `reseal-state`.

`reseal-state` is the explicit recovery command for committed-state ambiguity. It
must be planned and executed like any other Mother operation. Its plan must show
which local/remote seals were found, which live facts were used, which state is
being chosen as the new committed state, what superseded seals will be retained
for audit, and which replicas will receive the new seal.

Reseal must not be an automatic side effect of `diagnose`, `add-node`, or
`remove-node`. Those commands may report that reseal is required and print the
exact reseal command, but they must not invent a new committed state while
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

Prep owns the affected scopes until finalize or rollback.

### Do

Mother performs the prepared mutation.

Inputs:

- operation ID.

Outputs:

- checkpoint stream;
- current operation state;
- next allowed commands.

`do` must be restart-aware. If interrupted, rerunning `do` with the same
operation ID inspects the existing provisional frame and live assertions. It may
promote an already-complete step, retry a recognized partial state, resume from
the first unpromoted step, or safely report why it cannot continue. It must never
capture a replacement prestate over the interrupted result. The mandatory
idempotency contract is restore-to-prestate: every provisional or promoted frame
must remain safely restorable until its required closing event is committed.

### Finalize

Mother proves completion and closes the operation.

Inputs:

- operation ID.

Outputs:

- final verification report;
- released scopes;
- finalized operation record.

Finalize is the only success path that makes rollback unavailable.

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

Rollback must be available after `prep`, during/after `do`, and after failed
`finalize`, until the operation reaches `finalized`. An unresolved provisional
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

It must work from explicit selected services:

```text
mother reseal-qbft prep mainnet --nodes mainneta-super1,mainnetc-super1
mother reseal-qbft do --operation-id <id>
mother reseal-qbft finalize --operation-id <id>
```

`prep` for reseal must capture:

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

`do` for reseal may:

- stop validator subprocesses through guard-local runtime control;
- create backups of existing QBFT config and data;
- write the prepared QBFT config/genesis/topology;
- clear stale lifecycle markers captured in the plan;
- restart validator subprocesses.

`do` for reseal must not:

- rediscover a different desired validator set;
- include a newly running service that was not prepared;
- drop a selected service because it temporarily disappeared;
- delete Coolify services;
- recreate Coolify services;
- mutate compose;
- rebuild images.

`finalize` for reseal must prove:

- each selected service is still the same service identity captured during prep,
  unless the prepared plan explicitly allowed a restore-service dependency;
- each selected service still owns the expected validator address;
- every selected validator RPC is reachable;
- every selected node reports the same QBFT validator set;
- the reported QBFT validator set equals the prepared desired validator set;
- block production advances after restart;
- stale add/remove lifecycle markers are inactive or marked complete;
- rollback backups may now be retained for audit but are no longer part of an
  active rollback path.

`rollback` for reseal must restore the pre-operation local QBFT files and marker
state that `prep`/`do` captured, then restart validators in the previous mode. If
pre-operation state was not captured, rollback must refuse to pretend it can
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

`add-node prep` and `remove-node prep` must fail unless every expected Coolify
host proves:

- it is reachable;
- its committed journal/checkpoint and replayed state agree with the local head;
- it has no unresolved Mother action;
- it has no executable rollback frame;
- it has no provisional guard frame;
- it has no conflicting resource lock;
- it supports the required schema and capabilities.

Mother records the exact participant set in the action journal. The participant
set may include every current node, every voting validator, every affected
Coolify host, and the target node. No participant may be silently dropped after
prep.

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

`add-node prep` must calculate and record:

- the complete target service and identity plan;
- the mode: `initial`, `soft`, or `hard`;
- the current and desired validator sets;
- the current and desired host-local RPC route graphs;
- the current and desired Hub/FDB topology for every node;
- all affected participant and resource scopes;
- the complete ordered distributed rollback plan.

`add-node do` is ordered. Every captured frame or distributed layer is
promoted only after its complete forward poststate is freshly verified; the next
numbered phase may not begin before promotion commits.

1. Cross the distributed preparation barrier.
2. Capture the target service prestate and create or repair the service.
3. Capture identity prestate and install the reserved identity.
4. Capture runtime prestate and establish a healthy private candidate.
5. Capture validator-membership prestate for the frozen voter/observer manifest,
   including the before-set hash and baseline block.
6. Admit the validator using the prepared initial mode, guard-mediated soft-vote
   proposal, or hard mode.
7. Freshly prove all required receipts, network-wide desired-set agreement, and
   post-membership block progress.
8. Capture complete RPC-routing prestate on every affected Coolify host.
9. Reconcile complete host-local canonical RPC backend sets.
10. Freshly prove route ownership, backend membership, Traefik load, and expected
    public chain identity.
11. Capture complete Hub/FDB prestate on every current node, including the new
    node.
12. Reconcile complete Hub/FDB topology on every participant.
13. Freshly prove the same topology epoch, peers, forwarding entries, and
    handoff targets everywhere.
14. Replicate and verify the pending resulting state.
15. Mark the action `do-complete-pending-finalize`.

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

`add-node finalize` must freshly verify the complete active assertion set across
all participants. It then closes every rollback layer through the journaled
finalization protocol and commits/replicates the resulting network state.

### `remove-node`

`remove-node prep` must calculate and record:

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
2. Freshly verify that all surviving Hub, RPC, and validator participants are
   healthy enough to carry the resulting network.
3. Capture Hub/FDB prestate on every current node.
4. Reconcile Hub/FDB topology without the target and verify every survivor.
5. Capture RPC-routing prestate on every affected Coolify host.
6. Reconcile complete RPC backend sets without the target and verify surviving
   public RPC service.
7. Capture validator-membership prestate for the frozen voter/observer manifest
   while the target remains running and reachable.
8. Remove the validator using the prepared guard-mediated soft-vote proposal or
   hard mode.
9. Freshly prove all required receipts, target absence, complete desired-set
   agreement, and post-membership block progress.
10. Capture target service, runtime, and identity prestate.
11. Detach, disable, archive, or remove the target exactly as prepared.
12. Replicate and verify the pending resulting state.
13. Mark the action `do-complete-pending-finalize`.

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

`remove-node finalize` must freshly prove the complete active assertion set
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

After `do` succeeds, the network should look complete to the operator, but the
entire distributed action remains reversible. Only `finalize` closes rollback.

Finalization must prove:

- every expected host remains reachable;
- every participant still belongs to this action and has no foreign pending work;
- validator, RPC, and Hub/FDB states all match the prepared result;
- all guard assertions are fresh for current generations;
- every distributed rollback layer and participant frame is accounted for;
- every expected replica agrees on the pending resulting state.

The committed network-journal `pending-action-finalized` entry is the
irreversible boundary. After it commits, an opposite change is a new `add-node`
or `remove-node` action with new prestates and a new rollback stack.

### Repair-only route reconciliation

`rpc-propagate` or another explicit route-repair command may reconcile a damaged
route graph using the same typed prestate and rollback rules. It is not part of
the normal success path and must never be required after a successful node
action.

## Diagnosis report

A Mother diagnosis report is read-only and should contain at least:

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

Diagnosis must not decide to fix anything. It only reports facts, topology
classification, current operation ID, current stage, rollback availability, and
active operation constraints. The diagnosis report is the normal way for an
operator to learn which operation Mother currently owns and which finalize or
rollback command is allowed next.

## Operation file

A prepared Mother operation file should contain at least:

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
journals. `do` must not edit the desired state it was asked to perform.

Mother must treat the replayed action journal, rollback journal, and referenced
participant receipts as the source of truth for rollback. `rollback_stack` is an
inspectable LIFO projection of promoted frames, not an independently mutable
authority. A local control script must not ask the operator for rollback details.
If a local action needs a backup file, previous config, old route, service UUID,
or validator address in order to undo itself, that data must be durably captured
before the forward step is considered complete.

## Safety rules

Mother safety rules:

- Every mutating command has `prep`, `do`, and `finalize` stages.
- Every prepared operation accepts `rollback` until `finalize` succeeds.
- Mother stores the active operation ID as the current operation for every owned scope.
- `mother diagnose` must report the current operation ID and allowed next commands.
- Rollback defaults to the current operation; the operator must not have to describe what to undo.
- Mother owns the provisional frame, promoted rollback stack, and reverse
  execution order.
- No destructive forward step may run before its provisional rollback frame is
  durable.
- No frame may enter the active rollback stack until the forward poststate is
  freshly verified and promotion is durably committed.
- A failed or ambiguous step remains provisional and enters
  `remediation-required`.
- Partial rollback may pop only a contiguous number of promoted top layers, after
  resolving the provisional layer first.
- Every mutating operation declares affected scopes during `prep`.
- A scope may have only one active non-finalized operation.
- A conflicting command must be rejected until the active operation is finalized
  or rolled back.
- `prep` is the only stage that interprets operator intent.
- `do` performs only the prepared operation.
- `finalize` proves completion and releases the scope.
- `rollback` backs out a non-finalized operation or honestly reports why it
  cannot.
- Diagnosis is always read-only.
- Service count is never validator count.
- Coolify service existence is never proof of QBFT membership.
- QBFT membership is never proof that a Coolify service exists.
- Reseal is not a deployment operation.
- Only `add-node` and explicit `restore-service` may create or recreate a
  Coolify super-node service.
- Add/remove validator phases use the frozen guard-mediated QBFT proposal and
  participant manifest or explicit hard topology mode; they are not drift repair
  operations.
- A submitted or accepted validator vote is intermediate evidence only; the
  membership layer succeeds only after complete desired-set agreement and
  post-membership block progress are freshly proven.
- No command may silently switch operation kind.
- No command may silently widen scope.
- No command may hide mutation inside a verifier.
- No command may call a destructive helper from another lifecycle path.
- Add-node must establish internal service readiness before requesting validator
  admission.
- Add-node must not publish RPC routing before validator admission is proven.
- Add-node must not publish Hub/FDB topology before RPC reconciliation succeeds.
- Remove-node must withdraw Hub/FDB and RPC dependencies before validator
  removal and service deletion.
- Soft/hard topology mode is chosen during `prep` and may not change during `do`.
- Mother must distinguish observed topology, finalized topology, and replicated pending distributed state.
- Mother and guard mutation APIs must not be exposed through public Traefik routes.
- Remote operator access to local-only Mother APIs must use a Coolify/Allfather
  mediated call-runner or another explicitly trusted bootstrap transport.
- A call-runner is disposable transport. Killing it must not corrupt Mother
  state or be required to roll back a distributed node operation.

## Minimum implementation sequence

Mother should be implemented in this order:

1. Mother durable-state bootstrap
   - create `/runtime/state/mother/`;
   - create `/runtime/state/mother/identity.private.yaml`;
   - reserve network identity, officer/admin identities, node validator keys,
     validator addresses, first-genesis material, and route reservations;
   - create durable locations for actions, rollback stacks, routes, guards,
     locks, topology, and version/capability records;
   - store the initial private identity backend as inline local private YAML;
   - make any `*_key_ref` values internal references to records in the same
     private-state document.

2. Mother control API shell
   - mounts `/runtime/state/mother/`;
   - reports version, capabilities, state root, active operations, checkpoints,
     and rollback stacks;
   - treats the container and mounted API implementation as replaceable;
   - refuses mutating actions if the state schema is unknown.

3. Coolify-mediated local call-runner transport
   - keeps Mother and guard APIs local/private only;
   - prefers one-shot temporary call-runner services for remote operator calls;
   - accepts a persistent private call-runner only as disposable transport;
   - makes manual runner kill/restart/delete safe because authoritative state
     remains under `/runtime/state/mother/`;
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
   - generic rollback resolution.

6. `prep`
   - creates operation files;
   - declares scopes;
   - records desired state, mutation steps, prestate-capture and restore
     contracts, and postconditions;
   - does not fabricate live rollback frames before `do` captures actual prestate.

7. Stage runner
   - `do`;
   - checkpoints;
   - durable provisional-frame arming before destructive steps;
   - full poststate verification and atomic frame promotion;
   - remediation-required reports;
   - retry/resume using the existing frame.

8. Generic `rollback`
   - resolves the current operation automatically;
   - restores an unresolved provisional frame before promoted layers;
   - supports all, count, and through for a contiguous top-of-stack range;
   - uses the operation record, participant frames, and rollback stack as the
     rollback brain;
   - available until finalize;
   - releases scopes only after full rollback verification and
     current-operation pointer cleanup.

9. `finalize`
   - postcondition checks;
   - cross-journal finalization preparation;
   - network-journal promotion from pending desired topology to finalized
     topology;
   - full replica acknowledgement;
   - releases scopes;
   - closes rollback window.

10. Distributed QBFT membership controller
   - frozen proposal, voter, and observer manifests;
   - guard-mediated participant vote requests and durable receipt retrieval;
   - desired validator-set and post-membership block assertions;
   - compensating membership rollback using the captured before-set;
   - no promotion from `vote-submitted` alone.

11. Distributed route and Hub/FDB resource controllers
   - typed complete-prestate capture;
   - host-local RPC desired-state reconciliation;
   - node-local Hub/FDB desired-state reconciliation;
   - distributed rollback-layer accounting and assertions.

12. `mother_add_node.py`
   - service and identity preparation;
   - initial, guard-mediated soft, and hard validator admission;
   - RPC reconciliation;
   - network-wide Hub/FDB reconciliation;
   - one rollback-capable action until finalize.

13. `mother_remove_node.py`
   - network-wide Hub/FDB withdrawal;
   - RPC withdrawal;
   - guard-mediated soft and hard validator removal;
   - service detach/disable/archive/removal;
   - one rollback-capable action until finalize.

14. `mother_reseal_qbft.py`
   - full-set hard topology repair for existing services;
   - in-place guard-mediated config repair only;
   - no Coolify service deletion or compose changes.

15. `mother_restore_service.py`
   - explicit service repair only;
   - no QBFT membership mutation.


## Current operating lesson

The immediate lesson from the Allfather failures is:

```text
A lifecycle command without staged ownership is a half-transaction waiting to
happen.
```

Mother must not repeat that mistake.

The Mother rule is:

```text
First Mother is told what will happen.
Then Mother prepares an immutable operation record.
Then Mother does exactly that operation.
Then Mother finalizes it, or rolls it back.
Until finalize or rollback, Mother refuses a different story for the same scope.
```

For validator lifecycle work, that means:

```text
reseal-qbft:
  in-place config repair, staged as prep/do/finalize, rollback until finalize

add-node:
  service creation, validator admission, RPC routing, and Hub/FDB topology as
  one staged distributed action, rollback until finalize

remove-node:
  Hub/FDB withdrawal, RPC withdrawal, validator removal, and service removal as
  one staged distributed action, rollback until finalize

restore-service:
  explicit service repair, staged as prep/do/finalize, rollback until finalize
```

The control surface should make partial operations visible, retryable, and
rollback-aware. It should never make the operator guess whether the system is in
the middle of a story.
