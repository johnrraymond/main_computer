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

This use case is the reference story for Mother. It shows why service lifecycle
and network topology lifecycle are separate.

Goal:

```text
Start with no committed validator topology.
Create the first super-node on coolify-a.
Join that node to the empty topology.
Create a second super-node on coolify-c.
Join the second node to the running topology.
Remove coolify-a from the topology.
Delete/disable the coolify-a service.
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

# 1. Prepare the first service on coolify-a.
mother add-node prep mainnet --node mainneta-super1 --host coolify-a
mother add-node do mainnet
mother add-node finalize mainnet

# Result:
#   mainneta-super1 exists in standby service state.
#   Its reserved validator identity matches /runtime/state/mother/identity.private.yaml.
#   It is not yet in QBFT topology.
#   It is not in public RPC/Hub routing.

# 2. Join the first service to the empty chain topology.
mother join-topology prep mainnet --node mainneta-super1 --mode initial
mother join-topology do mainnet
mother join-topology finalize mainnet

# Result:
#   Mother installs the precomputed first-genesis material.
#   mainneta-super1 is the only validator in QBFT topology.
#   routing reflects the finalized topology policy.

# 3. Prepare a second service on coolify-c.
mother add-node prep mainnet --node mainnetc-super1 --host coolify-c
mother add-node do mainnet
mother add-node finalize mainnet

# Result:
#   mainnetc-super1 exists in standby service state.
#   It has its reserved validator identity.
#   It is not yet in QBFT topology.
#   It is not in public RPC/Hub routing.

# 4. Join the second service to the running chain topology.
mother join-topology prep mainnet --node mainnetc-super1 --mode soft
mother join-topology do mainnet
mother join-topology finalize mainnet

# Result:
#   QBFT topology contains both validator addresses.
#   public/internal routing policy includes mainnetc-super1 only after finalize.

# 5. Remove coolify-a's service from chain and route topology.
mother remove-topology prep mainnet --node mainneta-super1 --mode soft
mother remove-topology do mainnet
mother remove-topology finalize mainnet

# Result:
#   QBFT topology contains only mainnetc-super1's validator address.
#   public/internal RPC and Hub routing no longer target mainneta-super1.
#   mainneta-super1 is a detached/standby service, not an active validator.

# 6. Delete or disable the detached coolify-a service.
mother remove-node prep mainnet --node mainneta-super1
mother remove-node do mainnet
mother remove-node finalize mainnet

# Result:
#   mainneta-super1 is gone or disabled according to the prepared plan.
#   coolify-c's mainnetc-super1 is the solo effective network.
```

At every point after `prep` and before `finalize`, `mother diagnose mainnet`
must report the current operation ID, stage, owned scopes, completed checkpoints,
and allowed next commands. Rollback is generic:

```text
mother rollback mainnet
```

It resolves the active operation from the Mother control surface and unwinds the
durable rollback stack. The operator does not have to tell rollback which kind of
operation is active.

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
the durable source for identities, topology records, action journals, rollback
stacks, route before-state snapshots, guard observations, locks, and network
facts that must survive replacement of the Mother container or API
implementation.

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
3. `join-topology` installs or activates chain topology using Mother-owned facts.
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

Standby is the default service state after `add-node`. A standby service is not
in public RPC routing, public Hub routing, aggregate public routing, or QBFT
validator topology. Moving a service into standby removes it from public routing.
Leaving standby does not itself publish routes; route membership is committed by
`join-topology finalize`, `remove-topology finalize`, or a dedicated routing
operation.

`MOTHER-DESIGN-003: mother-owned-first-genesis`

The first network genesis is generated before Mother deploys or before the first
topology operation. `add-node` does not generate genesis. `join-topology --mode
initial` installs the Mother-owned first-genesis material when joining the first
node to an empty topology.

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
passes the private key only to the component that must sign or deploy. Topology
operations may create, delete, or repair services and routes, but they must not
delete, regenerate, or rotate these private-state identity records unless the
operator explicitly requests identity rotation.

`MOTHER-DESIGN-005: route-gated-standby-runtime`

`MOTHER-OPEN-002: standby-hub-runtime-behavior` is resolved as internal-only,
route-gated standby. A standby service may keep internal guard/runtime processes
available for diagnostics and recovery, but public Traefik routes must not point
at it. Entering standby withdraws RPC and Hub public routes and records the
previous route state in the rollback stack before the route change is applied.
Leaving standby does not publish routes by itself; public routing is committed by
a join, remove, or explicit routing operation.

`MOTHER-DESIGN-006: api-first-guard-runtime-control`

`MOTHER-OPEN-003: guard-runtime-api` is resolved as API-first runtime control.
High-level Mother actions are decomposed into ordered calls against the Mother
control surface and per-node guard endpoints. Runtime topology changes must not
depend on replacing compose files.

Compose may provision or replace the disposable service shell, but runtime
mutations are API operations. The guard API must expose idempotent primitives for
at least:

```text
POST /guard/v1/identity/install-reserved
POST /guard/v1/standby/enter
POST /guard/v1/qbft/config/install
POST /guard/v1/qbft/config/restore
POST /guard/v1/validator-rpc/start
POST /guard/v1/validator-rpc/stop
GET  /guard/v1/topology/state
```

Every successful mutating guard or routing API call must have a durable undo
packet pushed onto the operation rollback stack. The rollback stack must be
inspectable through the Mother API before, during, and after the action.

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

### Remaining open design nodes

No remaining open design nodes are defined in this baseline. New unknowns must
be added as explicit `MOTHER-OPEN-*` entries before code depends on them.


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
  join_topology.py
  remove_topology.py
  remove_node.py
  reseal_qbft.py
  restore_service.py
  rollback.py
  common/
    coolify.py
    guards.py
    qbft.py
    inventory.py
    topology.py
    routing.py
    private_state.py
    operations.py
    locks.py
    planning.py
    reporting.py
    rollback_stack.py
```

Recommended command shape:

```text
# Read-only.
python tools/mother/mother.py diagnose mainnet

# Service lifecycle. add-node defaults to standby; no --standby flag exists.
python tools/mother/mother.py add-node prep mainnet --node mainnetc-super1 --host coolify-c
python tools/mother/mother.py add-node do mainnet
python tools/mother/mother.py add-node finalize mainnet

# Chain/routing topology lifecycle.
python tools/mother/mother.py join-topology prep mainnet --node mainnetc-super1 --mode soft
python tools/mother/mother.py join-topology do mainnet
python tools/mother/mother.py join-topology finalize mainnet

python tools/mother/mother.py remove-topology prep mainnet --node mainneta-super1 --mode soft
python tools/mother/mother.py remove-topology do mainnet
python tools/mother/mother.py remove-topology finalize mainnet

# Service deletion after topology removal.
python tools/mother/mother.py remove-node prep mainnet --node mainneta-super1
python tools/mother/mother.py remove-node do mainnet
python tools/mother/mother.py remove-node finalize mainnet

# Generic rollback. Mother resolves the active operation from the control surface.
python tools/mother/mother.py rollback mainnet
python tools/mother/mother.py rollback mainnet --operation-id <id>

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
- pushing durable undo packets before or atomically with each successful
  mutating substep;
- exposing rollback for every non-finalized mutating operation;
- exposing the current action, checkpoints, and rollback stack through the API;
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

Mother must keep a durable operation ledger. This ledger can begin as files under
a Mother-owned persistent volume and later move to FDB or another durable store,
but the semantics must be stable.

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
  rollback/
    <operation-id>.json
  operations/
    mainnet/
      network/
        active.json
        <operation-id>.json
      services/
        mainneta-super1/
          active.json
          <operation-id>.json
        mainnetc-super1/
          active.json
          <operation-id>.json
  reports/
    <operation-id>/
      prep-report.json
      do-checkpoints.jsonl
      finalize-report.json
      rollback-report.json
```

The operation ledger is the place where Mother remembers what it has been told
will happen. The live infrastructure is not allowed to be reinterpreted as if the
prepared instruction never existed.

#### Current operation pointer

For each owned scope, Mother must also maintain a durable current-operation
pointer. The pointer is not advisory; it is the authority that says which
operation is active for that scope.

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
  "operation_path": "/runtime/state/mother/operations/mainnet/network/reseal-mainnet-001.json",
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
GET  /v1/networks/<network>/current-operation
GET  /v1/scopes/<scope>/current-operation
POST /v1/operations/<kind>/prep
POST /v1/current/<network>/do
POST /v1/current/<network>/finalize
POST /v1/current/<network>/rollback
POST /v1/operations/<operation-id>/do                 # optional cross-check form
POST /v1/operations/<operation-id>/finalize           # optional cross-check form
POST /v1/operations/<operation-id>/rollback           # optional cross-check form
GET  /v1/operations/<operation-id>
GET  /v1/operations/<operation-id>/checkpoints
GET  /v1/operations/<operation-id>/rollback-stack
GET  /v1/operations/<operation-id>/rollback-stack/<frame-id>
GET  /v1/guards/<node>/topology-state
```

The HTTP shape is optional; the stage semantics, state-root visibility, current
operation visibility, and rollback-stack visibility are not optional.

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
- calculate the rollback strategy for every step that may be performed in `do`;
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
- push the prepared undo packet before or atomically with each mutating substep;
- write a checkpoint before and after every mutation step;
- leave the operation in a state that can either be finalized or rolled back.

`do` must not:

- reinterpret operator intent;
- discover a different desired topology;
- add newly found services to the operation;
- remove missing services from the operation;
- change the operation kind;
- widen scope;
- switch from reseal to restore;
- switch from add to remove;
- silently call another lifecycle path.

If a step fails during `do`, Mother must leave the operation open and report the
next allowed commands:

```text
mother <kind> do --operation-id <id>        # retry idempotently
mother rollback <network> [--operation-id <id>]  # roll back as far as safely possible
```

### `finalize`

`finalize` proves that the prepared operation reached its declared final state.

`finalize` must:

- run the operation's postcondition checks;
- verify that all mutation checkpoints are complete;
- verify that the desired state matches the actual state;
- mark the operation complete;
- release all active scope ownership;
- make `rollback` unavailable for this operation, except as a new explicit
  recovery operation.

`finalize` must not perform hidden repair. If postconditions fail, `finalize`
must leave the operation open and report the allowed next commands:

```text
mother <kind> do --operation-id <id>        # retry or complete pending mutation
mother rollback <network> [--operation-id <id>]  # undo the non-finalized operation
```

### `rollback`

`rollback` is valid for every prepared operation until `finalize` succeeds.

`rollback` must:

- resolve the current active operation from Mother's current-operation pointer
  when the operator does not provide an operation ID;
- if an operation ID is provided, prove that it matches the current operation for
  the requested scope before doing anything;
- load the same prepared operation record;
- inspect completed checkpoints;
- pop and execute the durable rollback action stack in reverse order;
- run only rollback frames that Mother recorded before or during completed
  mutation steps;
- skip rollback actions for steps that never completed;
- verify the rollback target state;
- mark the operation rolled back;
- release all active scope ownership and clear current-operation pointers.

Rollback is not an operator-authored command list. The local scripts and guards
must not require the operator to say what to undo. Mother owns the rollback plan,
the rollback stack, the order of unrolling, and the verification of each undo
step. Local control scripts may expose primitive reversible actions, but Mother
decides which rollback action to call and with which stored payload.

`rollback` must be conservative. If it cannot safely undo a step, it must say so
and leave a clear manual recovery report. It must not pretend that a partial
rollback is clean.

After `finalize`, rollback is no longer a stage of the completed operation.
Changing the result of a finalized operation requires a new `prep`.

#### Rollback action stack

Every mutation step that can affect live state must have a corresponding
rollback frame before it is allowed to execute. The active rollback frame list is
durable state and must be inspectable through the Mother API.

A rollback frame is a durable instruction owned by Mother, for example:

```json
{
  "frame_id": "0003-disable-public-route",
  "stage_created": "do",
  "status": "armed",
  "scope": "service:mainneta-super1",
  "forward_action": {
    "kind": "route.disable",
    "target": "mainneta-super1"
  },
  "rollback_action": {
    "kind": "route.restore",
    "target": "mainneta-super1",
    "payload": {
      "previous_routes": ["https://..."]
    }
  },
  "verification": {
    "rollback_postcondition": "routes restored to previous_routes"
  }
}
```

Frames are pushed in forward execution order and popped in reverse execution
order. This makes rollback LIFO:

```text
forward:
  1. disable public route
  2. stop validator
  3. write QBFT config

rollback:
  1. restore previous QBFT config
  2. restart validator in previous mode
  3. restore public route
```

Mother must write or arm the rollback frame before the corresponding destructive
forward action. If a step cannot produce an adequate rollback frame, `do` must
refuse before running that step. If a remote guard applies a local change, the
guard response must include enough before-state or backup references for Mother
to record the rollback frame durably.

Rollback frames are part of the operation record. They must not live only in a
local shell script, terminal output, or transient container memory.

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
mother remove-validator prep mainnet --node mainneta-super1
mother add-validator prep mainnet --node mainnetd-super1
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
next commands. The normal allowed next commands are:

```text
mother <kind> finalize <network> [--operation-id <id>]
mother rollback <network> [--operation-id <id>]
```

The operator should not need to pass `--operation-id` for these commands because
Mother already knows the current operation for the network and scopes. When an
operation ID is shown, it is for auditability and cross-checking, not because
the operator is responsible for remembering the rollback target.


## Topology probe contract

The hard problem in Mother is not merely starting or stopping services. The hard
problem is knowing whether the observed service topology, process topology,
validator identity topology, and QBFT consensus topology agree well enough to
merge a node, prune a node, or repair drift.

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

Mother has two views of topology:

- **observed topology**: what probes see right now;
- **committed topology**: what finalized Mother operations have recorded as the
  intended state.

Observed topology is evidence. Committed topology is intent. Neither one may
silently overwrite the other.

`prep` compares observed topology with committed topology and records a planned
transition. `do` executes only that planned transition. `finalize` proves the
observed topology reached the planned state and then updates committed topology.
`rollback` restores the prior committed intent or reports exactly why that is no
longer possible.

Until `finalize` runs, the committed topology must not pretend the operation is
complete. If another command is requested for an overlapping scope, Mother must
reject it and print the active operation plus the allowed `finalize` or
`rollback` command.

### Topology change commands and modes

Topology mutation is not part of `add-node` or `remove-node`.

- `join-topology` adds an existing standby service to Besu/QBFT, RPC routing, and
  Hub routing.
- `remove-topology` removes an existing service from Besu/QBFT, RPC routing, and
  Hub routing.
- `reseal-qbft` repairs the entire selected QBFT topology in place.
- `add-node` creates or repairs service existence only.
- `remove-node` deletes or disables a service only after Mother proves it is no
  longer in committed topology.

Mother supports three topology-change modes. The operator's primary command must
choose or imply the mode during `prep`; `do` must not switch modes.

Initial topology change:

```text
Used only when committed chain topology is empty.
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
topology change is not service deployment. It may stop and restart validator
subprocesses, but it must not delete/recreate Coolify services, rebuild images,
or replace compose.

### Route gating

Public routes are part of topology, but they are not proof of consensus
membership. Route changes are Mother routing API operations with rollback frames,
not hidden compose side effects.

For add-node, public routes must stay disabled until the node is internally
healthy and admitted into the desired QBFT topology. For join-topology, publishing
RPC and Hub routes must be the last mutating substep of `do`. For
remove-topology, public routes may be withdrawn or drained early after `prep` and
before validator removal. For remove-node, the super-node service must not be
deleted until the chain topology no longer contains the target validator and
`finalize` proves the prune.

Mother must distinguish:

```text
internal topology ready
chain topology updated
public route topology enabled
service topology deleted
```

These are separate facts, not one readiness flag.

## Operation states

A Mother mutating operation uses explicit states:

```text
prepared
doing
do-failed
do-complete-pending-finalize
finalizing
finalize-failed
finalized
rolling-back
rollback-failed
rolled-back
```

Only these states accept commands:

```text
prepared:
  do, rollback

doing:
  do retry, rollback if checkpoints make rollback safe

do-failed:
  do retry, rollback

do-complete-pending-finalize:
  finalize, rollback

finalize-failed:
  finalize retry, do retry if missing mutation is identified, rollback

rollback-failed:
  rollback retry, manual recovery report

finalized:
  no further stage commands; create a new operation for further changes

rolled-back:
  no further stage commands; create a new operation for further changes
```

Read-only diagnosis is always allowed, but diagnosis must label active operations
that may affect interpretation.

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

Service creation or service repair only. It does not change QBFT topology.

Purpose:

- create or repair a super-node service;
- install the reserved identity from `/runtime/state/mother/identity.private.yaml`;
- bring the service to standby/internal-ready state;
- keep public routes disabled;
- keep the node out of QBFT topology;
- produce a service that can later be joined by `join-topology`.

Stage contract:

```text
mother add-node prep mainnet --node <service> --host <host>
mother add-node do mainnet
mother add-node finalize mainnet
mother rollback mainnet
```

`prep` must run a topology probe and record:

- current committed service topology;
- current observed service topology;
- target service name, host, ports, and route reservations;
- reserved validator identity from `/runtime/state/mother/identity.private.yaml`;
- whether the service already exists;
- whether the operation is a new deploy, repair, or invalid conflict;
- standby route policy;
- rollback frames for each planned mutation.

`do` performs only the prepared service plan:

1. create or repair the service;
2. install the reserved node identity;
3. ensure guard/internal diagnostics are reachable;
4. prove the validator key exists and derives the reserved validator address;
5. place the service in standby;
6. ensure public RPC/Hub/aggregate routes are absent or disabled;
7. ensure no QBFT admission or removal request was submitted.

Forbidden:

- submitting QBFT add-validator votes;
- starting Besu on guessed or wrong genesis;
- enabling public routes;
- treating service creation as validator admission;
- inventing validator identity at runtime;
- changing committed QBFT topology.

`finalize` must prove:

- service exists in the prepared location;
- guard identity matches the prepared service;
- validator identity matches Mother private state;
- the service is in standby;
- public routes are absent/disabled;
- the validator is not in QBFT topology unless it was already present before the
  operation and the plan explicitly classified this as service repair;
- committed service topology has been updated.

### `mother_join_topology.py`

Topology merge for an existing standby service.

Purpose:

- take a prepared/existing service and make it part of chain topology;
- install correct genesis/topology material;
- start validator RPC in the correct mode;
- update internal RPC and Hub routing;
- enable public routes only when the prepared routing policy allows it.

Stage contract:

```text
mother join-topology prep mainnet --node <service> --mode initial|soft|hard
mother join-topology do mainnet
mother join-topology finalize mainnet
mother rollback mainnet
```

`prep` must prove or record:

- service exists and is in standby/internal-ready state;
- validator identity matches `/runtime/state/mother/identity.private.yaml`;
- current observed QBFT topology;
- current committed topology;
- desired topology after join;
- selected mode: `initial`, `soft`, or `hard`;
- routing changes to be made only after topology success;
- rollback stack for each mutation.

`do` performs only the prepared topology join:

- `initial`: install Mother-owned first-genesis material and start the first
  validator;
- `soft`: start the candidate on correct genesis, submit live QBFT admission, and
  wait for validator-set inclusion;
- `hard`: quiesce selected validators, write the planned topology in place, and
  restart/verify them;
- update internal RPC/Hub routing according to the prepared plan;
- keep the operation open pending finalize.

Forbidden:

- creating or deleting Coolify services;
- inventing genesis during service startup;
- changing from initial/soft/hard after prep;
- publishing public routes before topology verification;
- considering `vote-requested` to be success.

`finalize` must prove:

- the joined validator is in QBFT topology;
- all reachable validators agree on the set;
- block height advances after the topology change;
- internal routing matches committed topology intent;
- public routes match the prepared route policy;
- committed topology has been updated.

### `mother_remove_topology.py`

Topology prune for an existing service. It does not delete the service.

Purpose:

- remove a service's validator from QBFT topology;
- remove or drain public/internal RPC and Hub routing for that node;
- leave the service detached/standby so it can be inspected, repaired, rejoined,
  or later deleted by `remove-node`.

Stage contract:

```text
mother remove-topology prep mainnet --node <service> --mode soft|hard
mother remove-topology do mainnet
mother remove-topology finalize mainnet
mother rollback mainnet
```

`prep` must prove or record:

- target service is explicit;
- target validator address is known;
- current QBFT validator set;
- survivor services and validator addresses;
- removing the target will not remove the final validator unless a hard plan
  explicitly defines the replacement topology;
- selected mode: `soft` or `hard`;
- public/internal route withdrawal plan;
- rollback stack for each mutation.

`do` performs only the prepared topology removal:

1. withdraw or drain target public routes as prepared;
2. request topology removal:
   - `soft`: live QBFT removal vote;
   - `hard`: offline in-place topology change;
3. verify the target validator is absent from the QBFT set;
4. verify survivor validators remain present;
5. update internal RPC/Hub routing according to the prepared plan;
6. leave the target service detached/standby.

Forbidden:

- deleting the service;
- removing the final validator by accident;
- inferring the target from ordinal or service count;
- treating service stop/delete as validator removal;
- hiding a hard reseal inside an ordinary soft remove.

`finalize` must prove:

- target validator is absent from QBFT topology;
- survivors agree on the validator set;
- block height advances after removal;
- target is absent from public/internal routing;
- target service still exists or is disabled in the prepared detached state;
- committed topology has been updated.

### `mother_remove_node.py`

Service deletion/disable only after topology removal.

Purpose:

- delete, disable, or archive a service that is already absent from committed
  topology;
- never perform QBFT membership changes;
- never use service deletion as consensus removal.

Stage contract:

```text
mother remove-node prep mainnet --node <service>
mother remove-node do mainnet
mother remove-node finalize mainnet
mother rollback mainnet
```

`prep` must prove or record:

- target service is explicit;
- target is absent from committed QBFT topology;
- target is absent from observed QBFT topology according to reachable validators,
  or the plan classifies the system as damaged and refuses automatic deletion;
- target is absent from public and internal route topology;
- target is not an FDB coordinator required by the current network plan;
- volume/archive policy is explicit;
- rollback stack exists for every reversible service mutation.

`do` performs only the prepared service deletion/disable/archive plan.

Forbidden:

- requesting QBFT votes;
- changing genesis or validator config;
- removing topology;
- deleting a service whose validator is still in QBFT topology;
- deleting a service whose routes are still active.

`finalize` must prove:

- service state matches the prepared deletion/disable/archive policy;
- no route topology points to the service;
- committed service topology has been updated;
- operation scopes have been released.

### Compatibility aliases

Mother may expose `add-validator` and `remove-validator` as aliases for the
validator-topology portion of `add-node` and `remove-node`, but the node lifecycle
scripts are the authoritative commands when Coolify services and public routes
are involved.

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

## Lifecycle state machine

The old lifecycle model was:

```text
observe -> mutate -> wait -> patch next error
```

Mother's lifecycle model is:

```text
diagnose -> prep -> do -> finalize
                 \-> rollback
```

`diagnose` is read-only and can be run at any time. `prep`, `do`, `finalize`, and
`rollback` are operation stages bound to an operation ID.

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

`do` must be idempotent. If interrupted, rerunning `do` with the same operation ID
must continue or safely report why it cannot continue.

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
- released scopes;
- rolled-back operation record.

Rollback must be available after `prep`, during/after `do`, and after failed
`finalize`, until the operation reaches `finalized`.

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

## Service and topology lifecycle contract

Mother separates service lifecycle from network topology lifecycle.

```text
add-node:
  service exists, standby by default, no QBFT topology change

join-topology:
  existing service joins Besu/QBFT plus RPC/Hub routing

remove-topology:
  existing service leaves Besu/QBFT plus RPC/Hub routing

remove-node:
  service is deleted/disabled only after topology removal is finalized

reseal-qbft:
  hard full-set in-place topology repair for selected existing services
```

### Standby service state

Standby is the default output of `add-node`. There is no `--standby` flag in the
normal path.

A standby service must have:

- service exists in Coolify or the chosen service backend;
- guard/internal diagnostics reachable;
- reserved validator identity installed;
- validator address matches `/runtime/state/mother/identity.private.yaml`;
- public RPC routes absent/disabled;
- public Hub routes absent/disabled;
- aggregate routes do not include the service;
- no QBFT admission/removal request active;
- validator not added to committed topology by this command;
- Besu either stopped or running only in an explicitly designed candidate mode
  that cannot accidentally join the wrong genesis.

### `add-node`

`add-node prep` must prove or record:

- no conflicting active operation owns the network, target service, target host,
  target route, or affected validator scopes;
- requested service name, host, and network are explicit;
- target validator identity is reserved in `/runtime/state/mother/identity.private.yaml`;
- current observed service topology and committed service topology have been
  captured;
- public routes for the new node will remain disabled;
- rollback plan is known before deployment begins.

`add-node do` is ordered:

1. Deploy or repair the disposable super-node service shell if needed.
2. Install reserved identity through the guard API.
3. Bring guard/internal diagnostics up.
4. Prove validator identity without requiring chain membership.
5. Push route-restore rollback frames.
6. Put the service in standby and withdraw routes through guard/routing APIs.
7. Keep the operation active and pending finalize.

`add-node finalize` must:

- re-run a topology probe;
- prove the service still owns the prepared validator address;
- prove the service is in standby;
- prove public routes are absent/disabled;
- update committed service topology;
- release operation scopes.

`add-node` must not request chain topology changes.

### `join-topology`

`join-topology prep` must prove or record:

- no conflicting active operation owns the network, target service, target route,
  or affected validator scopes;
- target service exists and is internally ready;
- target validator address matches Mother private state;
- current committed topology and observed QBFT topology;
- desired validator set after join;
- mode: `initial`, `soft`, or `hard`;
- route changes that will be applied only after topology success;
- rollback frames for each planned mutation.

`join-topology do` is ordered:

1. Prepare the node for the selected topology mode.
2. Apply topology change:
   - `initial`: install Mother-owned first genesis for an empty topology;
   - `soft`: submit live QBFT admission;
   - `hard`: perform offline in-place topology rewrite.
3. Verify the new validator is present in the QBFT set.
4. Verify all reachable validators agree.
5. Push route-restore rollback frames.
6. Update RPC and Hub routing according to the plan as the last mutating substep.
7. Keep the operation active and pending finalize.

`join-topology finalize` must:

- prove validator-set agreement;
- prove block height advances;
- prove routing state matches the prepared policy;
- update committed topology;
- release operation scopes.

### `remove-topology`

`remove-topology prep` must prove or record:

- no conflicting active operation owns the network, target service, target route,
  or affected validator scopes;
- target service is explicit;
- target validator address is known;
- current QBFT validator set;
- survivor validator set;
- desired validator set after removal;
- mode: `soft` or `hard`;
- route withdrawal plan;
- rollback frames for each planned mutation.

`remove-topology do` is ordered:

1. Push route-restore rollback frames.
2. Withdraw or drain target public routes as prepared.
3. Apply topology removal:
   - `soft`: submit live QBFT removal;
   - `hard`: perform offline in-place topology rewrite.
4. Verify target validator is absent from QBFT.
5. Verify survivors remain present and agree.
6. Remove target from internal RPC and Hub routing.
7. Put target service into detached/standby state through the guard API.
8. Keep the operation active and pending finalize.

`remove-topology finalize` must:

- prove target validator is absent;
- prove survivor validators remain present;
- prove block height advances;
- prove target is absent from route topology;
- update committed topology;
- release operation scopes.

`remove-topology` must not delete the service.

### `remove-node`

`remove-node prep` must prove or record:

- no conflicting active operation owns the target service;
- target service is explicit;
- target is absent from committed topology;
- target is absent from observed QBFT topology or automatic deletion is refused;
- target is absent from public and internal routing;
- deletion/disable/archive policy is explicit;
- rollback frames are available for reversible service mutations.

`remove-node do` executes only the prepared service deletion/disable/archive plan.

`remove-node finalize` must:

- prove service state matches the prepared plan;
- prove route topology still excludes the service;
- update committed service topology;
- release operation scopes.

`remove-node` must not request chain topology changes.

### Merge and prune invariants

Before merging a node into topology, Mother must know:

```text
service identity
reserved validator identity
current committed topology
current observed QBFT topology
desired topology
selected mode
route policy
rollback stack
```

Before pruning a node from topology, Mother must know:

```text
target service
target validator address
survivor validator addresses
current QBFT set
desired QBFT set
route withdrawal plan
selected mode
rollback stack
```

The system must never use Coolify service count as validator count.

### Public route sequencing

Public routes are topology outputs, not evidence of topology safety.

- `add-node` keeps routes disabled.
- `join-topology finalize` may enable routes if the prepared plan says so.
- `remove-topology do` may withdraw/drain routes before validator removal.
- `remove-node` requires routes already absent/disabled.


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

The operation file is immutable with respect to intent and desired state. Runtime
checkpoints may be appended, rollback stack frames may be appended/marked
complete, and stage/status may advance, but `do` must not edit the desired state
it was asked to perform.

Mother must treat `rollback_stack` as the source of truth for rollback. A local
control script must not ask the operator for rollback details. If a local action
needs a backup file, previous config, old route, service UUID, or validator
address in order to undo itself, that data must be captured in the operation
record before the forward step is considered complete.

## Safety rules

Mother safety rules:

- Every mutating command has `prep`, `do`, and `finalize` stages.
- Every prepared operation accepts `rollback` until `finalize` succeeds.
- Mother stores the active operation ID as the current operation for every owned scope.
- `mother diagnose` must report the current operation ID and allowed next commands.
- Rollback defaults to the current operation; the operator must not have to describe what to undo.
- Mother owns and executes the rollback action stack in reverse order.
- No destructive forward step may run before its rollback frame is durable.
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
- Restore-service is the only normal operation allowed to create or recreate
  Coolify services.
- Add/remove are live QBFT vote operations, not drift repair operations.
- No command may silently switch operation kind.
- No command may silently widen scope.
- No command may hide mutation inside a verifier.
- No command may call a destructive helper from another lifecycle path.
- Add-node must deploy internal service readiness before requesting chain topology change.
- Add-node must not enable public routes before the validator is admitted/merged into QBFT.
- Remove-node must not delete or recreate a service before the validator is pruned from QBFT.
- Soft/hard topology mode is chosen during `prep` and may not change during `do`.
- Mother must distinguish observed topology from committed topology.
- Mother and guard mutation APIs must not be exposed through public Traefik routes.
- Remote operator access to local-only Mother APIs must use a Coolify/Allfather
  mediated call-runner or another explicitly trusted bootstrap transport.
- A call-runner is disposable transport. Killing it must not corrupt Mother
  state or be required to roll back a topology operation.

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
     markers, active operations, observed topology, committed topology drift, and
     the current operation ID.

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
   - records desired state, mutation steps, rollback frames, and postconditions.

7. Stage runner
   - `do`;
   - checkpoints;
   - durable rollback frame push before destructive steps;
   - idempotent retries.

8. Generic `rollback`
   - resolves the current operation automatically;
   - uses the operation record and rollback stack as the rollback brain;
   - available until finalize;
   - releases scopes only after rollback verification and current-operation
     pointer cleanup.

9. `finalize`
   - postcondition checks;
   - committed topology updates;
   - releases scopes;
   - closes rollback window.

10. `mother_add_node.py`
   - standby service creation only;
   - installs reserved identity;
   - no QBFT topology mutation.

11. `mother_join_topology.py`
   - `initial` mode for first-node empty topology;
   - `soft` mode for live QBFT admission;
   - `hard` mode for offline in-place join.

12. `mother_remove_topology.py`
   - live or hard topology prune;
   - route withdrawal;
   - no service deletion.

13. `mother_remove_node.py`
   - service deletion/disable/archive only after topology removal.

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

add-validator:
  live admission, staged as prep/do/finalize, rollback until finalized admission

remove-validator:
  live removal, staged as prep/do/finalize, rollback until finalized removal

restore-service:
  service repair, staged as prep/do/finalize, rollback until finalized restore
```

The control surface should make partial operations visible, retryable, and
rollback-aware. It should never make the operator guess whether the system is in
the middle of a story.
