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

## Namespace

Everything new uses the Mother namespace.

Recommended layout:

```text
tools/mother/
  mother.py
  diagnose_qbft.py
  probe_topology.py
  plan_topology.py
  add_node.py
  remove_node.py
  reseal_qbft.py
  restore_service.py
  common/
    coolify.py
    guards.py
    qbft.py
    inventory.py
    topology.py
    state.py
    operations.py
    locks.py
    planning.py
    reporting.py
    rollback.py
```

Recommended command shape:

```text
python tools/mother/mother.py diagnose qbft mainnet

python tools/mother/mother.py add-node prep mainnet --node mainnetc-super1 --host coolify-c --mode soft
python tools/mother/mother.py remove-node prep mainnet --node mainneta-super1 --mode soft
python tools/mother/mother.py reseal-qbft prep mainnet --nodes mainneta-super1,mainnetc-super1

python tools/mother/mother.py do mainnet
python tools/mother/mother.py finalize mainnet
python tools/mother/mother.py rollback mainnet

python tools/mother/mother.py do mainnet --operation-id <id>        # optional cross-check
python tools/mother/mother.py finalize mainnet --operation-id <id>  # optional cross-check
python tools/mother/mother.py rollback mainnet --operation-id <id>  # optional cross-check
```

Names may change, but the stage contract must not change: every mutating Mother
operation starts with a kind-specific `prep`, then generic Mother `do`,
`finalize`, or `rollback` commands resolve the active operation from the Mother
control surface. `rollback` is not kind-specific. Once Mother has recorded an
operation as current, rollback must be able to unwind it from the durable
operation record without the caller restating what kind of operation is being
rolled back.

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

The Mother control container is a small, persistent operator service that owns
operation state and stage transitions. It is not a super-node and it is not part
of the QBFT validator set.

### Responsibilities

The Mother control container is responsible for:

- loading private-state and Coolify host configuration;
- discovering Coolify services and guard endpoints;
- querying guard status and QBFT RPC state;
- creating immutable prepared operation records;
- enforcing one active prepared operation per declared scope;
- rejecting conflicting commands until the active operation is finalized or
  rolled back;
- executing `do` only from a prepared operation record;
- exposing rollback for every non-finalized mutating operation;
- recording checkpoints during `do`;
- verifying postconditions during `finalize`;
- releasing operation ownership only during `finalize` or `rollback`;
- producing human-readable and JSON reports.

The Mother control container is not responsible for:

- running validator processes itself;
- holding validator keys;
- rebuilding super-node images;
- replacing Coolify compose files during normal validator lifecycle operations;
- deleting or recreating services except inside an explicit `restore-service`
  operation;
- treating service count as consensus truth.

### Persistent operation ledger

Mother must keep a durable operation ledger. This ledger can begin as files under
a Mother-owned persistent volume and later move to FDB or another durable store,
but the semantics must be stable.

Suggested ledger layout:

```text
/mother-state/
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
/mother-state/
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
  "operation_path": "/mother-state/operations/mainnet/network/reseal-mainnet-001.json",
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
GET  /v1/diagnose/<network>
GET  /v1/networks/<network>/current-operation
GET  /v1/scopes/<scope>/current-operation
POST /v1/operations/<kind>/prep
POST /v1/current/<network>/do
POST /v1/current/<network>/finalize
POST /v1/current/<network>/rollback
POST /v1/operations/<operation-id>/do          # optional cross-check form
POST /v1/operations/<operation-id>/finalize    # optional cross-check form
POST /v1/operations/<operation-id>/rollback    # optional cross-check form
GET  /v1/operations/<operation-id>
```

The HTTP shape is optional; the stage semantics are not optional.

All mutation requests must include an idempotency key. Repeating the same request
with the same idempotency key must return the same operation record or continue
the same operation. Repeating a request with a different intent for an occupied
scope must fail with a conflict.

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
next allowed commands using the current operation, not a kind-specific rollback
entrypoint:

```text
mother do <network> --operation-id <id>        # retry the current operation idempotently
mother rollback <network> --operation-id <id>  # unwind the current operation from its rollback stack
```

The `--operation-id` value is only a safety cross-check. Mother must be able to
derive the operation kind, affected scopes, completed forward steps, and rollback
actions from the current operation record on the Mother control surface.

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
mother do <network> --operation-id <id>        # retry or complete the current mutation
mother rollback <network> --operation-id <id>  # unwind the non-finalized current operation
```

Again, the kind comes from the operation record. The caller does not choose a
rollback implementation.

### `rollback`

`rollback` is valid for every prepared operation until `finalize` succeeds.

Rollback is a Mother control-surface command, not a command owned by
`add-node`, `remove-node`, or `reseal-qbft`. The caller must not have to know
which forward actions already happened. The caller should be able to say only:

```text
mother rollback <network>
```

Mother then resolves the current operation pointer for that network or scope,
loads the operation record, reads the operation kind from that record, and pops
the durable rollback stack. A supplied `--operation-id` is only an assertion that
the caller and Mother agree about the active operation; it is not instructions
for how to roll back.

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

#### Mother state is the rollback brain

The Mother control surface must persist enough information to drive rollback
without consulting the original caller. The operation record is the rollback
brain.

For each completed forward action, Mother must know:

- which handler applied the action;
- which machine, service, volume, route, validator key, marker, or config file
  was touched;
- what before-state or backup reference is needed to undo it;
- whether the undo action is automatic, partial, or manual-only;
- which verification proves the undo action succeeded;
- whether the rollback frame has already been popped, skipped, failed, or
  completed.

Local scripts and guards may implement small primitives such as
`validator.stop`, `validator.start`, `qbft_config.write`,
`qbft_config.restore_backup`, `route.disable`, or `route.restore`, but they do
not choose the rollback sequence. They return before-state, backup references,
and verification facts to Mother. Mother stores those facts before treating the
forward step as complete.

The rollback command must not accept a user-authored list of undo actions. It
must reject attempts to override the recorded rollback stack unless the operator
starts a new explicit recovery operation.

#### Rollback action stack

Every mutation step that can affect live state must have a corresponding
rollback frame before it is allowed to execute.

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
  mother finalize mainnet --operation-id reseal-mainnet-001
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
mother finalize <network>
mother rollback <network>
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

### Soft and hard topology changes

Mother supports two chain topology-change modes. The operator's primary command
must choose or imply the mode during `prep`; `do` must not switch modes.

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
drift repair. A hard topology change is not service deployment. It may stop and
restart validator subprocesses, but it must not delete/recreate Coolify services,
rebuild images, or replace compose.

### Route gating

Public routes are part of topology, but they are not proof of consensus
membership. For add-node, public routes must stay disabled until the node is
internally healthy and admitted into the desired QBFT topology. For remove-node,
public routes may be disabled early after `prep`, but the super-node service must
not be deleted until the chain topology no longer contains the target validator
and `finalize` proves the prune.

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
mother do <network> --operation-id <id>
mother finalize <network> --operation-id <id>
mother rollback <network> --operation-id <id>
```

Only `prep` is kind-specific. `do`, `finalize`, and `rollback` resolve the
operation kind from Mother's current operation record.

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

Node deployment plus validator topology merge.

Purpose:

- add a super-node service by deploying it to the internal network first;
- keep public routes disabled while the node is joining;
- prove the service owns a validator address and can run internally;
- request the chain topology change in the operator-selected mode;
- enable public routes only after the chain topology includes the node and
  `finalize` proves the operation complete.

Stage contract:

```text
mother add-node prep ...
mother do <network> --operation-id <id>
mother finalize <network> --operation-id <id>
mother rollback <network> --operation-id <id>
```

Only `prep` is kind-specific. `do`, `finalize`, and `rollback` resolve the
operation kind from Mother's current operation record.

`prep` must run a topology probe and record:

- current committed topology;
- current observed service topology;
- intended new service name, host, ports, routes, and validator role;
- whether the service already exists;
- whether the operation is a new deploy, recovery of a prepared-but-unfinalized
  deploy, or an invalid conflict;
- desired chain topology-change mode: `soft` or `hard`;
- route policy: public routes disabled until finalize;
- rollback model for each planned step.

`do` performs only the prepared plan:

1. deploy or update the super-node only to the internal topology;
2. wait for guard, FDB, Hub, and validator RPC to be internally reachable;
3. read and record the validator address from the running service;
4. request the chain topology change:
   - `soft`: submit the live QBFT admission request;
   - `hard`: perform the prepared offline in-place topology change;
5. wait until the desired QBFT validator set includes the new validator;
6. leave public routes disabled.

Forbidden:

- enabling public routes before chain topology contains the validator;
- treating service creation as validator admission;
- treating `vote-requested` as success;
- silently changing from soft to hard or hard to soft;
- deleting unrelated services;
- finalizing before topology agreement is observed.

`finalize` must prove:

- the service exists and is running;
- guard identity matches the prepared service;
- validator address matches the prepared operation;
- validator address is in the QBFT set;
- all reachable validators agree on the set;
- block height advances after the topology change;
- public routes are enabled only if the prepared operation requested them;
- committed topology has been updated.

Rollback expectation:

- before chain topology change is requested, remove or disable the staged service
  according to the prepared rollback plan;
- after a soft admission request but before admission finalizes, clear pending
  admission markers and disable/delete the staged service as planned;
- after admission is finalized into QBFT, automatic rollback must refuse to
  invent an inverse operation and must require a prepared `remove-node`;
- after a hard topology change, restore captured backups if and only if the prep
  snapshot and do-stage backups are available.

### `mother_remove_node.py`

Validator topology prune followed by service removal.

Purpose:

- remove one explicit super-node from the chain topology;
- keep enough survivor validators to maintain the network;
- request the chain topology change in the operator-selected mode;
- delete the super-node service only after the target validator is absent from
  the finalized QBFT topology.

Stage contract:

```text
mother remove-node prep ...
mother do <network> --operation-id <id>
mother finalize <network> --operation-id <id>
mother rollback <network> --operation-id <id>
```

Only `prep` is kind-specific. `do`, `finalize`, and `rollback` resolve the
operation kind from Mother's current operation record.

`prep` must run a topology probe and record:

- explicit target service name;
- target Coolify host and service UUID;
- target validator address;
- current QBFT validator set;
- survivor services and survivor validator addresses;
- whether all reachable validators agree;
- whether block height is advancing;
- desired chain topology-change mode: `soft` or `hard`;
- route policy for the target;
- service deletion policy after finalized prune;
- rollback model for each planned step.

`do` performs only the prepared plan:

1. optionally disable target public routes according to the prepared plan;
2. request the chain topology change:
   - `soft`: submit the live QBFT removal request;
   - `hard`: perform the prepared offline in-place topology change;
3. wait until the target validator is absent from the QBFT set;
4. verify every survivor validator remains present;
5. only then delete or disable the target super-node service as prepared.

Forbidden:

- deleting the target service before the target validator is absent from QBFT;
- removing the last validator;
- inferring the target from highest ordinal;
- treating service count as validator count;
- treating service deletion as validator removal;
- silently changing from soft to hard or hard to soft;
- stopping survivor validators in soft mode;
- using reseal to hide an ordinary remove operation.

`finalize` must prove:

- target validator is absent from the QBFT validator set;
- survivor validators remain present;
- all reachable validators agree on the set;
- block height advances after removal;
- target service route state and service deletion/disabled state match the
  prepared plan;
- stale removal markers are complete or inactive;
- committed topology has been updated.

Rollback expectation:

- before chain topology change is requested, re-enable target public routes or
  restore target service settings if prep changed them;
- after a soft removal request but before removal finalizes, clear pending
  removal markers and restore target route/service settings;
- after target removal is finalized into QBFT, automatic rollback must refuse to
  invent an inverse operation and must require a prepared `add-node`;
- if service deletion has already occurred after finalized prune, rollback must
  not pretend it can restore the validator without a `restore-service` or
  `add-node` operation.

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
mother restore-service rollback --operation-id <id>
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
mother do mainnet --operation-id <id>
mother finalize mainnet --operation-id <id>
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

## Add/remove node topology contract

`add-node` and `remove-node` are not merely service operations. They are staged
topology transitions that move a node through service topology, internal runtime
topology, QBFT consensus topology, and public route topology in a safe order.

Both commands must begin with a topology probe. Neither command may rely on
service count as validator count. Neither command may use a stale lifecycle marker
as proof that a chain topology change succeeded.

### Add node

`add-node prep` must prove or record:

- no conflicting active operation owns the network, target service, target host,
  target route, or affected validator scopes;
- the requested service name, host, and network are explicit;
- current observed topology and committed topology have been captured;
- existing validators agree on the current QBFT validator set unless the operator
  explicitly chose hard mode;
- the desired mode is recorded as `soft` or `hard`;
- public routes for the new node will remain disabled until finalize;
- the rollback plan is known before deployment begins.

`add-node do` is ordered:

1. Deploy the super-node up to internal readiness only.
   - service exists;
   - guard reachable;
   - FDB/HUB local requirements satisfied;
   - validator RPC running;
   - validator address known;
   - public routes disabled.
2. Request the chain topology change according to the prepared mode.
   - soft mode submits live QBFT admission;
   - hard mode performs the prepared offline in-place topology change.
3. Verify the chain topology includes the new validator.
   - all reachable validators agree on the set;
   - block height advances after admission/reseal;
   - stale admission/reseal markers are not active contradictions.
4. Keep the operation active and pending finalize.

`add-node finalize` must:

- re-run a topology probe;
- prove the new service still owns the prepared validator address;
- prove the validator is in the QBFT set;
- prove block height advances;
- enable public routes only if requested by the prepared plan;
- update committed topology;
- release operation scopes.

`rollback` for an active `add-node` operation must:

- if topology change was not requested, remove or disable the staged service
  according to the prepared rollback plan;
- if soft admission was requested but not accepted, clear pending admission
  markers and remove/disable the staged service according to plan;
- if hard mode wrote config, restore captured backups if available;
- if the validator is already finalized into the QBFT set, refuse automatic
  rollback and require `remove-node prep`.

### Remove node

`remove-node prep` must prove or record:

- no conflicting active operation owns the network, target service, target route,
  or affected validator scopes;
- the target service is explicit;
- the target service exists unless the operation is explicitly a topology-only
  cleanup;
- target validator address is known;
- target validator is in the current QBFT set;
- at least one non-target survivor validator remains;
- every declared survivor validator maps to a known service;
- current validators agree on the current QBFT validator set unless the operator
  explicitly chose hard mode;
- block height is advancing unless the operator explicitly chose hard mode;
- the desired mode is recorded as `soft` or `hard`;
- service deletion is planned only after validator removal is proven;
- rollback plan is known before any route or service change begins.

`remove-node do` is ordered:

1. Optionally disable public routes for the target if the prepared plan says so.
2. Request the chain topology change according to the prepared mode.
   - soft mode submits live QBFT removal;
   - hard mode performs the prepared offline in-place topology change.
3. Verify the chain topology no longer contains the target validator.
   - all reachable survivor validators agree on the set;
   - survivor validators remain present;
   - block height advances after removal/reseal.
4. Only after the target is absent from QBFT, delete or disable the super-node
   service according to the prepared plan.
5. Keep the operation active and pending finalize.

`remove-node finalize` must:

- re-run a topology probe;
- prove the target validator is absent from the QBFT set;
- prove survivor validators are present and agreed;
- prove block height advances;
- prove target service state matches the prepared deletion/disabled policy;
- update committed topology;
- release operation scopes.

`rollback` for an active `remove-node` operation must:

- if topology change was not requested, restore target route/service settings;
- if soft removal was requested but not accepted, clear pending removal markers
  and restore target route/service settings;
- if hard mode wrote config, restore captured backups if available;
- if the target is already finalized absent from QBFT, refuse automatic rollback
  and require `add-node prep` or `restore-service prep` depending on whether the
  service still exists.

### Merge and prune invariants

A node is not merged when the service exists. A node is merged only when the
service's validator address appears in the agreed QBFT validator set and finalize
updates committed topology.

A node is not pruned when public routes are disabled. A node is pruned only when
the service's validator address is absent from the agreed QBFT validator set and
finalize updates committed topology. The service may be deleted only after that
absence has been proven during `do`.

### Public route sequencing

For add-node:

```text
deploy internal service
prove internal readiness
change chain topology
prove validator is in QBFT
enable public routes during finalize if requested
```

For remove-node:

```text
optionally disable public routes
change chain topology
prove validator is absent from QBFT
delete/disable service according to plan
finalize committed topology
```

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
      "mother finalize mainnet --operation-id reseal-mainnet-001",
      "mother rollback mainnet --operation-id reseal-mainnet-001"
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
    "/mother-state/current/mainnet.json",
    "/mother-state/current/scopes/network_mainnet.json",
    "/mother-state/current/scopes/service_mainneta-super1.json",
    "/mother-state/current/scopes/service_mainnetc-super1.json"
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
    "mother do mainnet --operation-id reseal-mainnet-001",
    "mother rollback mainnet --operation-id reseal-mainnet-001"
  ]
}
```

The operation file is immutable with respect to intent and desired state. Runtime
checkpoints may be appended, rollback stack frames may be appended/marked
complete, and stage/status may advance, but `do` must not edit the desired state
it was asked to perform.

Mother must treat `rollback_stack` as the source of truth for rollback. A local
control script must not ask the operator for rollback details and must not
require a kind-specific rollback command. If a local action needs a backup file,
previous config, old route, service UUID, validator address, marker contents, or
any other before-state in order to undo itself, that data must be captured in
the operation record before the forward step is considered complete.

A rollback invocation resolves like this:

```text
operator command:
  mother rollback mainnet

Mother resolution:
  1. read current-operation pointer for network:mainnet
  2. load operation_id from that pointer
  3. load operation.kind from the operation record
  4. inspect completed checkpoints and rollback_stack
  5. execute recorded rollback frames in reverse order
  6. mark the operation rolled back or partially rolled back
  7. clear current-operation pointers only when safe
```

## Safety rules

Mother safety rules:

- Every mutating command has `prep`, `do`, and `finalize` stages.
- Every prepared operation accepts generic Mother `rollback` until `finalize` succeeds.
- Mother stores the active operation ID as the current operation for every owned scope.
- `mother diagnose` must report the current operation ID and allowed next commands.
- Rollback defaults to the current operation; the operator must not have to describe what to undo or name the operation kind.
- Mother owns and executes the rollback action stack in reverse order.
- Local scripts expose reversible primitives; Mother chooses the rollback sequence from saved state.
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

## Minimum implementation sequence

Mother should be implemented in this order:

1. `mother_diagnose.py` and `mother_probe_topology.py`
   - read-only;
   - no locks;
   - reports services, guards, validators, QBFT sets, route state, lifecycle
     markers, active operations, observed topology, and committed topology drift.

2. Mother operation ledger and scope lock model
   - durable operation files;
   - current-operation pointers per network and scope;
   - active operation records;
   - conflict detection;
   - idempotency keys.

3. `mother_plan.py` / `prep`
   - creates operation files;
   - declares scopes;
   - records desired state, mutation steps, rollback steps, and postconditions.

4. Stage runner
   - `do`;
   - checkpoints;
   - durable rollback frame push before destructive steps;
   - idempotent retries.

5. `rollback`
   - resolves the current operation automatically;
   - checkpoint-aware;
   - rollback-stack-aware;
   - available until finalize;
   - releases scopes only after rollback verification and current-operation pointer cleanup.

6. `finalize`
   - postcondition checks;
   - releases scopes;
   - closes rollback window.

7. `mother_reseal_qbft.py`
   - in-place guard-mediated config repair only;
   - no Coolify service deletion or compose changes.

8. `mother_add_node.py` and `mother_remove_node.py`
   - topology-probed node merge/prune;
   - internal service readiness before add-node chain request;
   - chain topology prune before remove-node service deletion;
   - soft/hard mode selected at prep.

9. Optional `mother_add_validator.py` and `mother_remove_validator.py`
   - validator-only aliases for live QBFT voting;
   - no service lifecycle mutation;
   - refuse drifted topology.

10. `mother_restore_service.py`
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
