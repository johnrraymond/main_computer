# FDB operator surface

Status: operator-facing companion to `fdb.md`

Source reviewed: `fdb.md` SHA-256
`fe0dd9fcbe702c3d7b04bbb6a8ba2072f59e4a5f6de300d735d929561d39df1f`

## 1. Purpose and authority

This document is the top-level operator contract for FDB Control. It defines
which operation to choose, what each operation is allowed to change, the stages
an operator drives, the safety boundary, and what proves that the requested
FoundationDB state has actually been reached.

`fdb.md` remains the authority for the FDB architectural boundary and high-level
system invariants. If this document conflicts with `fdb.md`, `fdb.md` governs.

The command examples use:

```text
fdb-control
```

as shorthand for the eventual FDB Control entry point. The final Python module
path is intentionally not frozen by this document.

The operator surface is designed so that implementation can change without
changing operator meaning. Container layout, Coolify API adapters, SSH helpers,
FDB CLI wrappers, Python classes, and module boundaries belong to later
functionality and module documents.


## 2. External dependencies and operational independence

FDB Control is operationally independent of Mother.

FDB Control MUST NOT require any of the following in order to inspect or operate
FoundationDB:

- a running Mother control surface;
- Mother operation state;
- Mother journals, heads, checkpoints, or topology evidence;
- Besu validator membership;
- Mother replica membership;
- a Mother lifecycle operation;
- Hub lifecycle state.

FDB Control and Mother do, however, share a lower-level dependency on the same
private operator infrastructure configuration.

The current canonical shared private infrastructure file is:

```text
runtime/state/mother/identity.private.yaml
```

That shared file is not the FDB topology authority and is not a reason to make
FDB a child of Mother. Its purpose at the FDB boundary is to provide private
infrastructure facts needed to reach and control deployment targets, including
such things as Coolify host identity, controller URL, and private credential or
token material.

The dependency shape is:

```text
                 runtime/state/mother/identity.private.yaml
                         /                     \
                        /                       \
                       v                         v
                 Mother Control             FDB Control
                       |                         |
                       v                         v
                     CHAIN                      FDB
```

This is the same canonical private identity document Mother consumes. FDB Control
reads it only as shared infrastructure input; it does not treat Mother private
state as FDB topology authority and does not derive FDB membership from Mother,
Besu, or Mother lifecycle state.

The division of authority is:

```text
shared privates file
    tells FDB Control what private infrastructure exists and how to reach it

FDB accepted topology
    tells FDB Control which FDB services are intended to exist on that infrastructure

live FDB discovery
    tells FDB Control what actually exists and participates now
```

FDB Control MUST NOT opportunistically rewrite the shared privates file as a
side effect of ordinary FDB operations. If an operation requires an
infrastructure target that has no usable private binding, the operation stops
before live mutation and reports the missing binding. A future explicit
infrastructure-enrollment contract may change that rule; none exists in this
operator surface.

Secret values from the shared privates file MUST NOT be copied into ordinary
operation evidence, console diagnostics, consumer contracts, or logs.


## 3. Operator object model

FDB Control uses the following terms consistently.

### 3.1 Cluster

A `cluster` is one logical FoundationDB database lineage with one cluster
identity and one accepted FDB configuration.

Cluster identity is not inferred from the number of containers currently
running.

### 3.2 Host

A `host` is a physical or virtual placement target and a failure domain relevant
to FDB availability analysis.

A host is resolved through shared private infrastructure configuration when
private deployment access is required.

A host is not an FDB member merely because it is capable of running FDB.

### 3.3 Service

A `service` is the independently managed FDB deployment unit placed on a host.

Multiple FDB services MAY be placed on the same host:

```text
host A
  +-- service A1
  +-- service A2

host B
  +-- service B1
```

The service, not the host, is the normal membership unit exposed by FDB Control.

### 3.4 Process

A `process` is an FDB runtime process belonging to a deployed service. A service
may result in one or more runtime processes depending on the final deployment
model.

A running process is not, by itself, proof that the process has joined the
intended logical cluster correctly.

### 3.5 Coordinator

A `coordinator` is an FDB coordinator endpoint selected from the deployed FDB
runtime topology.

Coordinator membership is independent of service membership. Normal service
topology mutations derive a quorum overlay from the resulting services and
distinct failure domains. They MUST NOT make every service a coordinator or
derive coordinator count from raw service count. Any derived coordinator change
MUST be frozen by `prep` before mutation.

### 3.6 Accepted topology

The `accepted topology` is the topology FDB Control most recently finalized as
intended.

It describes intended cluster membership and placement. It is not replaced
merely because a service becomes unreachable.

### 3.7 Observed topology

The `observed topology` is the topology discovered from current deployment state
and FoundationDB's own live view.

Accepted and observed topology are intentionally distinct.

### 3.8 Consumer contract

The `consumer contract` is the canonical Hub-facing description of how a Hub
must connect to and consume the current FDB cluster.

It is derived from FDB state. It does not grant FDB Control authority to mutate
Hub services.


## 4. Operator action classes

There are three operator action classes.

| Class | Operations | Meaning |
|---|---|---|
| Read-only observation | `inspect`, `consumer-contract` | Establish or derive state without changing FDB, accepted topology, or Hub state |
| Authoritative FDB mutation | `create-cluster`, `add-service`, `remove-service`, `replace-service`, `set-coordinators`, `configure-cluster`, `retire-cluster` | Intentionally change FDB cluster state or accepted FDB authority |
| Restoration to accepted intent | `reconcile` | Restore live deployment/runtime state to the already accepted FDB topology without intentionally changing membership |

`consumer-contract` is read-only even when it reports that Hub-FDB
rectification is required.


## 5. Common mutation lifecycle

Every mutating FDB operation uses the same operator-driven lifecycle:

```text
prep
  |
  v
do
  |
  v
finalize
```

There is no rollback stage.

The generic form is:

```text
fdb-control <operation> prep <network> ...
fdb-control <operation> do <network> [--operation-id <id>]
fdb-control <operation> finalize <network> [--operation-id <id>]
```

The exact option spelling for operation-specific inputs can be refined before
implementation freeze, but the behavioral separation between `prep`, `do`, and
`finalize` is normative.

### 5.1 Prep

`prep` is read-only with respect to live FDB and accepted topology.

It:

1. discovers current deployment and FDB state;
2. loads the relevant shared private infrastructure bindings without exposing
   their secret values;
3. validates the operator's requested intent;
4. identifies affected services, hosts, failure domains, coordinators, and FDB
   configuration;
5. calculates safety constraints and expected postconditions;
6. determines whether the requested transition is representable by the selected
   operation;
7. freezes the exact desired poststate and operation scope;
8. derives the expected class of consumer-contract impact when it can be known
   before mutation;
9. records the immutable prepared operation plan and prints the plan.

`prep` MUST NOT perform live FDB mutation merely to make the plan easier to
satisfy.

### 5.2 Do

`do` executes only the frozen plan.

It:

1. loads the prepared operation;
2. revalidates the frozen authority, infrastructure bindings, and preconditions
   required to execute safely;
3. refuses if observed state has changed in a way that invalidates the frozen
   transition;
4. performs only the bounded FDB/deployment mutations authorized by the plan;
5. observes enough FDB behavior to decide whether each mutation step actually
   took effect;
6. records progress so the same `do` can be retried after interruption.

`do` MUST NOT reinterpret the operator's intent, widen service membership,
automatically add coordinators, or choose a different target topology merely to
obtain success.

Retrying `do` uses the same operation ID and frozen target.

### 5.3 Finalize

`finalize` freshly observes the resulting deployment and FDB state and proves the
requested postcondition.

It:

1. verifies cluster identity;
2. verifies the operation-specific topology/configuration postcondition;
3. proves required FDB participation and availability properties;
4. verifies coordinator state when relevant;
5. verifies the accepted failure-domain and redundancy consequences required by
   the operation;
6. derives the actual post-operation consumer contract;
7. compares pre-operation and post-operation consumer-contract identities;
8. records whether Hub-FDB rectification is required;
9. commits the resulting accepted FDB topology/configuration only after the
   required postconditions are proved;
10. writes terminal operation evidence.

Live mutation does not become accepted topology merely because `do` returned
success.

The authority boundary is:

```text
live mutation
    -> fresh observation
    -> verified postcondition
    -> finalize
    -> accepted FDB state
```


## 6. Forward-only failure, retry, and resume semantics

FDB Control does not implement rollback.

A partial distributed mutation creates a real state. FDB Control responds by
observing that state and moving forward safely rather than pretending the exact
previous distributed state can always be reconstructed.

The normal interruption flow is:

```text
interrupted or failed operation
    -> inspect
    -> determine actual current state
    -> retry exact do, retry exact finalize, reconcile accepted topology,
       or prepare a new explicit forward transition when the old target is no
       longer the intended target
```

The following rules apply:

1. Retry `do` with the same operation ID when the frozen target remains valid and
   execution is incomplete.
2. Retry `finalize` with the same operation ID when live state appears to have
   reached the frozen target but terminal verification or acceptance was
   interrupted.
3. Use `reconcile` only when the already accepted topology is still the desired
   topology and runtime/deployment state has drifted away from it.
4. Use a new explicit mutation when the intended topology itself has changed.
5. Never use a generic rollback or repair operation to guess what state should
   exist.

Before mutating implementation ships, the operation-record contract MUST define
how an incomplete prepared operation is explicitly superseded when the operator
chooses a different forward transition. That bookkeeping mechanism is not a new
high-level FDB operation.


## 7. Complete operation catalog

### 7.1 Summary

| Operation ID | Operation | Operator intent | Class | Stages |
|---|---|---|---|---|
| `FDB-OP-INSPECT` | `inspect` | Establish and verify the current FDB state | Read-only | One shot |
| `FDB-OP-CREATE-CLUSTER` | `create-cluster` | Establish a new logical FDB cluster lineage | Authoritative mutation | `prep` / `do` / `finalize` |
| `FDB-OP-ADD-SERVICE` | `add-service` | Add one FDB service at an explicit host placement | Authoritative mutation | `prep` / `do` / `finalize` |
| `FDB-OP-REMOVE-SERVICE` | `remove-service` | Intentionally retire one FDB service | Authoritative mutation | `prep` / `do` / `finalize` |
| `FDB-OP-REPLACE-SERVICE` | `replace-service` | Replace one FDB service with another | Authoritative mutation | `prep` / `do` / `finalize` |
| `FDB-OP-SET-COORDINATORS` | `set-coordinators` | Establish the exact desired coordinator set | Authoritative mutation | `prep` / `do` / `finalize` |
| `FDB-OP-CONFIGURE-CLUSTER` | `configure-cluster` | Change FoundationDB logical database configuration | Authoritative mutation | `prep` / `do` / `finalize` |
| `FDB-OP-RECONCILE` | `reconcile` | Restore live state to already accepted topology | Restoration | `prep` / `do` / `finalize` |
| `FDB-OP-CONSUMER-CONTRACT` | `consumer-contract` | Derive the canonical Hub-facing FDB dependency | Read-only | One shot |
| `FDB-OP-RETIRE-CLUSTER` | `retire-cluster` | Deliberately decommission the entire FDB lineage | Authoritative mutation | `prep` / `do` / `finalize` |

No operation implies a hard-coded number of hosts, services, coordinators, or
Hubs.


## 8. `FDB-OP-INSPECT` -- `inspect`

### 8.1 Intent

Use `inspect` to establish what is actually true now.

Conceptual command:

```text
fdb-control inspect <network>
```

### 8.2 Required observations

`inspect` SHOULD produce one coherent report containing at least:

- logical cluster identity;
- whether a cluster is accepted, unborn, retired, or not safely identifiable;
- accepted topology;
- observed deployment topology;
- FoundationDB's observed live topology;
- hosts involved and host-level failure domains;
- services and service-to-host placement;
- runtime processes beneath each service when discoverable;
- reachable, unreachable, missing, extra, and ambiguous services;
- coordinator membership;
- coordinator reachability;
- whether required coordinator majority is presently available;
- current FoundationDB database configuration;
- current redundancy state;
- database availability;
- recovery/degraded state exposed by FDB;
- data movement or recovery activity when it materially affects a safe next
  operation;
- remaining machine/failure-domain tolerance when it can be established
  reliably;
- accepted-versus-observed drift;
- active or incomplete FDB Control operation state;
- current consumer-contract identity/hash when derivable;
- whether the accepted consumer contract and current derivation differ;
- whether Hub-FDB rectification is indicated;
- unknowns that prevent a safe conclusion;
- the next operation classes that remain permissible from the observed state.

### 8.3 Verification behavior

`inspect` is not a container listing command.

It SHOULD perform bounded verification sufficient to answer whether the database
can actually be used, including a safe transaction/read-write probe when the
cluster is expected to be writable and such a probe can be made without
changing application semantics.

There is no separate high-level `verify`, `diagnose`, `status`, or `topology`
operation. Those are facets of `inspect`.

### 8.4 Forbidden effects

`inspect` MUST NOT:

- change FDB configuration;
- add, remove, replace, start, or stop a service merely to improve the report;
- change coordinator membership;
- rewrite accepted topology;
- rewrite the shared privates file;
- rewrite Hub configuration;
- mark a missing service as intentionally retired;
- finalize or supersede an incomplete mutation.


## 9. `FDB-OP-CREATE-CLUSTER` -- `create-cluster`

### 9.1 Intent

Use `create-cluster` to establish a new logical FoundationDB cluster lineage.

It is not a generic deployment command.

Conceptual commands:

```text
fdb-control create-cluster prep <network> ...
fdb-control create-cluster do <network> [--operation-id <id>]
fdb-control create-cluster finalize <network> [--operation-id <id>]
```

### 9.2 Prep contract

`prep` freezes at least:

- network/logical cluster identifier;
- intended new cluster identity or the exact rule by which it will be created;
- initial service identities;
- each service's host placement;
- host-level failure domains;
- advertised/listen addresses required by the chosen placement;
- explicit initial coordinator set;
- requested initial FoundationDB configuration;
- the shared private infrastructure bindings required to deploy to each host;
- the expected first consumer-contract derivation class.

The initial number of services and hosts is operator/deployment policy, not a
constant in FDB Control.

`prep` MUST refuse new-cluster creation when evidence indicates that an existing
cluster lineage may already exist and cannot safely be distinguished from the
requested new lineage.

### 9.3 Do contract

`do` creates only the frozen initial services and establishes only the frozen
cluster/coordinator/configuration target.

It MUST NOT silently adopt an unrelated existing FDB cluster because its
services happen to be reachable.

### 9.4 Finalize contract

`finalize` proves at least:

- the intended logical cluster identity exists;
- every required initial service participates in that cluster as intended;
- the explicit initial coordinator set is active;
- the requested FDB configuration is active;
- the database accepts the required bounded usability probe;
- the resulting topology is sufficient for the requested configuration;
- the first accepted topology/configuration can be committed;
- the first canonical consumer contract can be derived.

### 9.5 Forbidden effects

`create-cluster` MUST NOT:

- infer initial membership from all hosts in the privates file;
- infer coordinator membership from all services;
- overwrite an existing logical cluster merely because the requested name is the
  same;
- create Hub services;
- modify chain state.


## 10. `FDB-OP-ADD-SERVICE` -- `add-service`

### 10.1 Intent

Use `add-service` to add exactly one FDB service to an existing logical cluster.

Canonical normal commands:

```text
fdb-control add-service prep <network> --service <service-id>
fdb-control add-service do <network> --operation-id <id>
fdb-control add-service finalize <network> --operation-id <id>
```

The normal `add-service` surface accepts the logical service identity only. It
does not require the operator to supply a Coolify host, FDB address, or FDB
port. Those placement details are resolved during `prep` and become part of the
frozen target.

### 10.2 Required topology semantics

Adding a service is not adding a host.

Both of these are valid:

```text
host A
  A1

add-service of a logical service whose placement token resolves to host A

host A
  A1
  A2
```

and:

```text
host D
  no FDB service

add-service of a logical service whose placement token resolves to host D

host D
  D1
```

### 10.3 Prep contract

The normal service identity for automatic placement is:

```text
<network><placement-token>-fdb<positive-ordinal>
```

For example:

```text
mainneta-fdb1 -> placement token a
mainnetc-fdb3 -> placement token c
```

`prep` MUST reject a service whose network prefix does not match the requested
network, whose placement token is absent, or whose placement token does not
resolve to exactly one configured Coolify controller. A service identity such as
`mainnetc-fdb3` therefore identifies logical placement intent; it is not itself
a private infrastructure record.

Placement resolution is deterministic:

1. parse the service identity and extract the placement token;
2. resolve that token against `networks.<network>.coolify.controllers.coolify-<token>` in the shared private identity file;
3. require exactly one configured controller named `coolify-<token>`;
4. derive the FDB-routable host address from the first usable configured value
   in this priority order: `fdb_vpn_ip`, `vpn_ip`, `private_vpn_ip`,
   `wireguard_ip`, `tailscale_ip`;
5. allocate the first unused accepted FDB port on that host starting at `4550`;
6. resolve the Coolify deployment binding for the resulting host;
7. freeze every resolved value into the prepared operation.

Address candidates that are loopback, unspecified, path-like, or otherwise
not usable as cross-host FDB addresses MUST be rejected. Port allocation uses
accepted topology as its authority baseline. If live observation contradicts
accepted placement strongly enough to make allocation ambiguous, prep stops
instead of silently choosing around the drift.

`prep` freezes at least:

- exact new service identity;
- parsed placement token and ordinal;
- exact resolved target host;
- required shared private infrastructure binding for that host;
- resolved FDB-routable address and its non-secret source reference;
- allocated FDB port and complete endpoint;
- deployment/runtime parameters owned by the service placement contract;
- accepted prestate generation;
- expected host/failure-domain consequences;
- source coordinator set;
- derived target coordinator set for the resulting service/failure-domain topology;
- explicit `coordinators_changed` decision frozen before effects.

`do` and `finalize` MUST consume these frozen values. They MUST NOT rerun
placement inference or port allocation.

`prep` MUST prove that adding this service cannot accidentally create or join the
wrong logical cluster.

### 10.4 Do contract

`do` creates only the frozen service on the frozen host and connects it to the
existing intended FDB cluster. It first proves the new service participates
through the source coordinator set. If prep froze a changed coordinator overlay,
it then applies and proves that exact coordinator set and resulting cluster
connection information.

A container becoming `running` is not sufficient success.

### 10.5 Finalize contract

`finalize` proves at least:

- the service exists at the intended host placement;
- its runtime address is the intended address;
- it participates in the intended logical FDB cluster;
- FDB has accepted the new process/service into the current topology as
  appropriate;
- the database remains usable;
- required convergence/recovery conditions are satisfied before the service is
  added to accepted topology;
- coordinator membership remains exactly as intended;
- the post-operation consumer contract is derived and compared with the prior
  contract.

### 10.6 Forbidden effects

`add-service` MUST NOT:

- create a new logical cluster;
- add every unused host;
- make every added service a coordinator merely because it was added;
- choose a coordinator target after `prep`;
- modify sibling FDB services on the target host except when a bounded shared
  host-level change is unavoidable and frozen by prep;
- mutate Hub configuration.


## 11. `FDB-OP-REMOVE-SERVICE` -- `remove-service`

### 11.1 Intent

Use `remove-service` to intentionally retire exactly one FDB service from the
accepted topology.

Conceptual commands:

```text
fdb-control remove-service prep <network> --service <service-id> ...
fdb-control remove-service do <network> [--operation-id <id>]
fdb-control remove-service finalize <network> [--operation-id <id>]
```

### 11.2 Prep contract

Before live mutation, `prep` MUST evaluate the resulting cluster without the
target service.

At minimum it evaluates:

- database availability consequences;
- configured redundancy consequences;
- service-count consequences;
- host/failure-domain consequences;
- coordinator consequences;
- current recovery or data-movement state that makes removal unsafe;
- whether the target service is still required to satisfy the accepted
  configuration;
- whether the target is presently a coordinator.

`prep` derives the target coordinator overlay from the surviving services and
their distinct failure domains. If the target service is a coordinator, the
resulting set MUST be frozen as part of this same prepared operation; it is not
a separate prerequisite command. The derived policy preserves valid surviving
coordinators first, selects at most one coordinator per failure domain, and uses
the largest odd cardinality supported by the surviving distinct failure domains
(minimum one).

### 11.3 Do contract

`do` performs only the frozen service retirement sequence. If the frozen
coordinator overlay changes, it first moves and proves coordinator authority and
records the actual rewritten cluster connection information. Only then may it
exclude/drain and delete the target service.

It MUST NOT remove sibling services that happen to share the same host.

An unreachable service is not treated as removed merely because it cannot be
contacted.

### 11.4 Finalize contract

`finalize` proves at least:

- the target service no longer participates in the intended active FDB topology;
- sibling services remain as accepted;
- coordinator state equals the exact frozen target overlay;
- the database remains usable when continued availability is required by the
  frozen plan;
- the resulting redundancy and failure-domain state matches the frozen accepted
  postcondition;
- accepted topology can safely drop the service;
- consumer-contract impact is derived.

### 11.5 Forbidden effects

`remove-service` MUST NOT:

- remove an entire host merely because one service on it is removed;
- remove sibling services implicitly;
- choose or change coordinator targets after `prep`;
- convert temporary unreachability into intentional retirement;
- retire the entire cluster.


## 12. `FDB-OP-REPLACE-SERVICE` -- `replace-service`

### 12.1 Intent

Use `replace-service` when one accepted service is being replaced by another and
the operator intends one continuous replacement transition rather than two
unrelated membership decisions.

Conceptual commands:

```text
fdb-control replace-service prep <network> \
  --old-service <service-id> \
  --new-service <service-id> \
  --new-host <host-id> ...

fdb-control replace-service do <network> [--operation-id <id>]
fdb-control replace-service finalize <network> [--operation-id <id>]
```

### 12.2 Prep contract

`prep` freezes at least:

- service being replaced;
- replacement service identity;
- replacement host placement;
- replacement network identity/addressing;
- whether address identity is intentionally retained or intentionally changes;
- host/failure-domain consequences;
- coordinator implications;
- safe participation/convergence threshold the replacement must reach before
  the old service can be retired;
- consumer-contract impact expected when connection information changes.

### 12.3 Do contract

`do` may internally perform bounded add, convergence, coordinator, and removal
steps required by the frozen replacement plan.

The central ordering rule is:

> The old service MUST NOT be intentionally retired until the replacement has
> reached the frozen safe participation/convergence threshold, unless the old
> service is already irrecoverably absent and the prepared plan explicitly
> describes the forward recovery from that observed state.

If replacement changes the failure-domain topology, `prep` MUST derive and
freeze the resulting coordinator overlay. If the old service is a coordinator,
coordinator authority moves to that frozen target only after the replacement has
reached its safe participation threshold and before the old service is retired.

### 12.4 Finalize contract

`finalize` proves at least:

- replacement service participates correctly;
- old service is no longer accepted as active;
- resulting host/failure-domain state is the frozen target;
- coordinator state is the frozen target;
- database usability and required redundancy properties hold;
- accepted topology records one completed replacement transition;
- consumer-contract impact is derived.

### 12.5 Forbidden effects

`replace-service` MUST NOT:

- become a generic rebalance command;
- replace additional services opportunistically;
- silently alter unrelated coordinator membership;
- reinterpret a host replacement as permission to remove every service on the
  old host.


## 13. `FDB-OP-SET-COORDINATORS` -- `set-coordinators`

### 13.1 Intent

Use `set-coordinators` as an advanced override to establish one explicit desired
coordinator set without changing service membership.

Conceptual commands:

```text
fdb-control set-coordinators prep <network> --coordinator <service-or-endpoint> ...
fdb-control set-coordinators do <network> [--operation-id <id>]
fdb-control set-coordinators finalize <network> [--operation-id <id>]
```

For this override operation the desired resulting set is operator intent. FDB
Control MUST NOT reinterpret it as the normal topology-derived policy or infer it
from all services, service numbering, or a hard-coded count.

### 13.2 Prep contract

`prep` freezes:

- exact desired coordinator set;
- mapping from each requested coordinator to a valid deployed/reachable FDB
  endpoint;
- host/failure-domain placement of those coordinators;
- current coordinator set;
- safety constraints for the transition;
- expected cluster connection-information effect;
- expected consumer-contract impact.

### 13.3 Do contract

`do` performs only the supported FDB transition from the observed/frozen
coordinator prestate to the explicit desired set.

### 13.4 Finalize contract

`finalize` proves at least:

- FoundationDB reports the intended coordinator configuration;
- the resulting coordinator set is reachable through the network model required
  by cluster participants;
- the database remains usable;
- cluster connection information is freshly derived;
- the consumer contract is freshly derived;
- old and new consumer-contract hashes/identities are compared;
- `Hub-FDB rectification required: yes|no` is recorded.

### 13.5 Forbidden effects

`set-coordinators` MUST NOT:

- add/remove service membership merely to obtain the requested coordinator
  count;
- select coordinators automatically unless a future explicit policy operation
  is added;
- mutate Hub configuration itself.


## 14. `FDB-OP-CONFIGURE-CLUSTER` -- `configure-cluster`

### 14.1 Intent

Use `configure-cluster` to change FoundationDB's own logical database
configuration.

Conceptual commands:

```text
fdb-control configure-cluster prep <network> ...
fdb-control configure-cluster do <network> [--operation-id <id>]
fdb-control configure-cluster finalize <network> [--operation-id <id>]
```

### 14.2 Scope

This operation may govern FDB-native configuration such as supported:

- redundancy mode;
- storage-engine or storage-policy configuration;
- other FoundationDB database configuration accepted into the later
  functionality contract.

It is not a generic deployment-settings command.

The following are outside its scope:

- Docker CPU or memory limits;
- arbitrary Coolify metadata;
- Hub configuration;
- Hub scaling;
- Besu configuration;
- arbitrary host configuration;
- shared privates-file edits.

### 14.3 Prep contract

For every requested FDB configuration change, `prep` MUST prove that the
observed services and independent failure domains can support the requested
postcondition before live mutation begins.

The requested resulting FDB configuration is frozen explicitly.

### 14.4 Do contract

`do` applies only the frozen FDB-native configuration transition and observes
FDB's response/convergence.

### 14.5 Finalize contract

`finalize` proves at least:

- the requested FDB configuration is active;
- current service and failure-domain topology satisfies the accepted
  configuration requirements;
- the database is in the required usable state;
- any required recovery/data movement has reached the frozen acceptance
  threshold;
- consumer-contract impact is derived.


## 15. `FDB-OP-RECONCILE` -- `reconcile`

### 15.1 Intent

Use `reconcile` when accepted FDB topology remains correct but live deployment
or runtime state has drifted away from it.

Conceptual commands:

```text
fdb-control reconcile prep <network> [--service <service-id> ...]
fdb-control reconcile do <network> [--operation-id <id>]
fdb-control reconcile finalize <network> [--operation-id <id>]
```

### 15.2 Defining invariant

`reconcile` does not intentionally change accepted membership.

Example:

```text
accepted:
  host A -> A1
  host B -> B1
  host C -> C1

observed:
  A1 present
  B1 present
  C1 missing
```

If `C1` remains accepted and no explicit operation retired or replaced it,
`reconcile` may restore `C1` and prove that it rejoins the same logical cluster.

By contrast:

```text
accepted:
  A1 B1 C1

operator intends:
  A1 B1 D1
```

is not reconciliation. It is a replacement/membership transition.

### 15.3 Prep contract

`prep` freezes:

- accepted topology being restored;
- exact observed drift;
- services/placements that require restoration;
- private infrastructure bindings required to restore them;
- proof that the operation does not intentionally change membership,
  coordinators, or FDB logical configuration;
- consumer-contract consequences expected if runtime connection material must be
  rematerialized without changing its canonical contract.

### 15.4 Do contract

`do` performs only the bounded restoration required to make runtime/deployment
state conform to accepted topology.

### 15.5 Finalize contract

`finalize` proves at least:

- required accepted services exist at their accepted placements;
- services participate in the already accepted logical cluster;
- no new service membership has been invented;
- coordinator set remains accepted;
- FDB logical configuration remains accepted;
- database usability is restored or confirmed;
- accepted topology itself does not advance merely because runtime drift was
  repaired, except for operation/evidence metadata required to record the
  successful reconciliation;
- consumer-contract state is freshly checked.

### 15.6 Forbidden effects

`reconcile` MUST NOT:

- convert an operator's desired topology change into an implicit repair;
- replace a service with a differently identified service unless the accepted
  topology already defines that identity equivalence;
- remove accepted services because they are temporarily unreachable;
- add coordinators;
- change redundancy mode.


## 16. `FDB-OP-CONSUMER-CONTRACT` -- `consumer-contract`

### 16.1 Intent

Use `consumer-contract` to derive the authoritative FDB information required by
the separate Hub-FDB rectification layer.

Conceptual command:

```text
fdb-control consumer-contract <network>
```

### 16.2 Required contract content

The final schema belongs to later functionality/module design, but the operator
contract requires enough canonical information to express at least:

- logical FDB cluster identity;
- Hub-reachable cluster connection information;
- coordinator/bootstrap information required by FDB clients;
- network addresses valid from Hub hosts rather than container-local-only
  addresses;
- FDB client/server compatibility requirements;
- FDB API-version requirement;
- TLS/transport requirements and public certificate references when used;
- application namespace/input values that properly belong at this dependency
  boundary;
- consumer-contract generation or equivalent identity;
- deterministic canonical hash;
- FDB topology/configuration generation references sufficient to explain what
  the contract was derived from.

Secret private key material, private controller tokens, and unrelated operator
credentials MUST NOT appear in the consumer contract.

### 16.3 Determinism

Given the same accepted FDB state and the same relevant dependency inputs,
`consumer-contract` MUST produce the same canonical contract bytes/hash.

### 16.4 Hub boundary

`consumer-contract` does not mutate Hubs.

Its result is consumed by a separate operator-runnable Hub-FDB rectification
surface.

The relationship is:

```text
FDB Control
  -> canonical consumer contract
  -> hub-fdb-rectify
  -> Hub FDB-facing configuration
```

### 16.5 Contract-change meaning

FDB topology generation and consumer-contract generation are not required to
advance together.

Example:

```text
add non-coordinator service

FDB topology changed: yes
consumer contract changed: no
Hub-FDB rectification required: no
```

Another example:

```text
set new coordinators

FDB topology changed: yes
consumer contract changed: yes
Hub-FDB rectification required: yes
```


## 17. `FDB-OP-RETIRE-CLUSTER` -- `retire-cluster`

### 17.1 Intent

Use `retire-cluster` to deliberately decommission one entire logical FDB cluster
lineage.

It is not equivalent to removing the final observed container.

Conceptual commands:

```text
fdb-control retire-cluster prep <network> ...
fdb-control retire-cluster do <network> [--operation-id <id>]
fdb-control retire-cluster finalize <network> [--operation-id <id>]
```

### 17.2 Prep contract

`prep` MUST prove explicit whole-cluster retirement intent and freeze:

- logical cluster identity being retired;
- complete accepted service membership;
- complete accepted coordinator set;
- current usability/reachability state;
- retirement sequence;
- resulting terminal consumer-contract state;
- policy for retained durable data and service volumes.

Temporary unavailability, zero reachable services, missing deployment state, or
an empty container listing MUST NOT be interpreted as retirement intent.

### 17.3 Do contract

`do` performs only the frozen decommissioning transition.

### 17.4 Finalize contract

`finalize` proves and records the terminal lineage state and prevents ordinary
membership/configuration operations from silently treating the retired lineage
as an active cluster.

### 17.5 Data destruction is not implicit

Retiring a logical cluster does not automatically mean securely destroying all
surviving storage bytes.

The exact durable-data destruction policy MUST be explicit before implementation
of destructive volume wiping. Until then, `retire-cluster` may stop and withdraw
services while preserving retained data according to the frozen retirement
plan.


## 18. Consumer-contract impact is a shared mutation postcondition

Every successful mutating operation MUST derive the post-operation consumer
contract and compare it with the pre-operation contract whenever a pre-operation
contract exists.

Terminal mutation evidence reports at least:

```text
FDB topology changed: yes|no
FDB configuration changed: yes|no
consumer contract changed: yes|no
Hub-FDB rectification required: yes|no
```

A topology change does not, by itself, imply Hub rectification.

A consumer-contract change does imply that the Hub-FDB dependency must be
reconciled before the Hub pool can be assumed to consume the new FDB dependency
correctly.

FDB Control reports that requirement; it does not perform Hub rectification.


## 19. State domains that MUST remain distinct

FDB Control MUST keep the following domains separate:

```text
shared private infrastructure configuration
prepared operation target
accepted FDB topology/configuration
observed deployment state
FoundationDB live-reported state
consumer contract
Hub applied FDB configuration
```

None of the following substitutions are valid:

- a privates-file host entry is not proof of FDB membership;
- a Compose/service definition is not proof of live FDB participation;
- a running container is not proof of correct logical-cluster membership;
- current FDB live membership is not automatically accepted topology;
- accepted topology does not override contradictory live evidence;
- a consumer contract is not the FDB topology itself;
- a changed FDB topology does not automatically mean a changed consumer
  contract;
- a changed consumer contract does not prove the Hubs have applied it.


## 20. Allowed next action by operation state

The exact persistence schema for operation states belongs to later design, but
the operator semantics are:

| State | Allowed operator action |
|---|---|
| No active mutation | `inspect`, `consumer-contract`, or prepare one valid mutation |
| `prepared` | Exact operation `do`; `inspect` |
| `doing` / interrupted do | Retry exact operation `do`; `inspect` |
| `do-incomplete` | `inspect`; retry exact `do` if frozen target remains valid; otherwise follow the explicit forward-supersession contract once defined |
| `do-complete-pending-finalize` | Exact `finalize`; `inspect` |
| `finalizing` / interrupted finalize | Retry exact `finalize`; `inspect` |
| `finalize-failed` | `inspect`; retry exact `finalize` when the target is reached; retry exact `do` only when inspection proves execution remains incomplete |
| `finalized` | `inspect`, `consumer-contract`, or prepare a new valid operation |
| Drift with no intentional topology change | `inspect`, then `reconcile prep` when accepted topology remains desired |

There is no rollback action in any state.

`inspect` remains available in every state and remains read-only.


## 21. Fast operation selector

| What the operator wants or observes | Use |
|---|---|
| Understand current FDB state, health, topology, degradation, or why a mutation is blocked | `inspect` |
| Create a brand-new logical FDB database lineage | `create-cluster` |
| Put another FDB service on any host, including a host already running FDB | `add-service` |
| Intentionally retire one service while retaining the cluster | `remove-service` |
| Substitute one service for another on the same or a different host | `replace-service` |
| Change exactly which endpoints are FDB coordinators | `set-coordinators` |
| Change FDB-native redundancy/storage/database policy | `configure-cluster` |
| Accepted topology is still right but services/runtime/deployment drifted | `reconcile` |
| Produce exactly what the Hub-FDB rectifier must know about FDB | `consumer-contract` |
| Deliberately decommission the entire logical FDB lineage | `retire-cluster` |


## 22. Lifecycle examples

The names below are illustrative only. Correctness MUST NOT depend on these
service IDs, hosts, counts, or operation order.

### 22.1 Create a three-service cluster on three hosts

```text
fdb-control inspect mainnet

fdb-control create-cluster prep mainnet \
  --service fdb-a1@host-a \
  --service fdb-b1@host-b \
  --service fdb-c1@host-c \
  --coordinator fdb-a1 \
  --coordinator fdb-b1 \
  --coordinator fdb-c1 \
  ...

fdb-control create-cluster do mainnet
fdb-control create-cluster finalize mainnet
fdb-control inspect mainnet
```

The exact composite service-option syntax is not frozen. The example establishes
operator intent only.

### 22.2 Add a second service to an existing host

If placement token `a` resolves to `coolify-a`, the normal operator request is:

```text
fdb-control add-service prep mainnet --service mainneta-fdb2
fdb-control add-service do mainnet --operation-id <prepared-id>
fdb-control add-service finalize mainnet --operation-id <prepared-id>
```

`prep` derives `coolify-a`, the host's configured FDB-routable address, and the
first unused accepted FDB port. For example, if `:4550` is already occupied on
that host, the new service is frozen at `:4551`.

Expected topology can now include:

```text
host-a
  fdb-a1
  fdb-a2

host-b
  fdb-b1

host-c
  fdb-c1
```

A coordinator change is implied only when the resulting distinct failure-domain topology changes the frozen derived overlay. Adding another service to the same failure domain does not by itself change coordinators.

### 22.3 Grow service count without growing host count

Starting topology:

```text
3 services / 3 hosts
```

Operator may independently add:

```text
fdb-a2 -> host-a
fdb-b2 -> host-b
```

Result:

```text
5 services / 3 hosts
```

`inspect` must still report only three independent host failure domains.

### 22.4 Replace a failed service onto another host

```text
fdb-control inspect mainnet

fdb-control replace-service prep mainnet \
  --old-service fdb-b1 \
  --new-service fdb-d1 \
  --new-host host-d \
  ...

fdb-control replace-service do mainnet
fdb-control replace-service finalize mainnet
```

If `fdb-b1` was a coordinator, prep derives and freezes the coordinator overlay
for the replacement post-topology. The do stage may move to that exact target,
but it cannot choose a different set after effects begin.

### 22.5 Remove one of two services from the same host

```text
host-a
  fdb-a1
  fdb-a2
```

The operator may run:

```text
fdb-control remove-service prep mainnet --service fdb-a2
fdb-control remove-service do mainnet
fdb-control remove-service finalize mainnet
```

Success does not remove `fdb-a1` or retire `host-a`.

### 22.6 Change coordinators without changing service membership

```text
fdb-control set-coordinators prep mainnet \
  --coordinator fdb-a1 \
  --coordinator fdb-c1 \
  --coordinator fdb-d1

fdb-control set-coordinators do mainnet
fdb-control set-coordinators finalize mainnet
```

Service membership can remain unchanged while cluster connection information and
the consumer contract change.

### 22.7 Restore a missing accepted service

```text
accepted:
  fdb-a1@host-a
  fdb-b1@host-b
  fdb-c1@host-c

observed:
  fdb-a1 present
  fdb-b1 present
  fdb-c1 deployment missing
```

Use:

```text
fdb-control reconcile prep mainnet --service fdb-c1
fdb-control reconcile do mainnet
fdb-control reconcile finalize mainnet
```

Do not use `add-service`, because membership is not changing.

### 22.8 Interrupted add-service resumes forward

```text
fdb-control inspect mainnet
fdb-control add-service do mainnet --operation-id <same-id>
```

If inspection instead proves the service reached the frozen target and only
acceptance was interrupted:

```text
fdb-control add-service finalize mainnet --operation-id <same-id>
```

There is no rollback command.

### 22.9 Topology changes but Hub contract does not

```text
add-service finalized within an already represented failure domain
FDB topology generation advances
derived coordinator overlay unchanged
consumer-contract hash unchanged
Hub-FDB rectification required: no
```

### 22.10 Coordinator change requires Hub rectification

```text
add-service / remove-service / replace-service / set-coordinators finalized
frozen coordinator overlay changed
FDB topology/configuration accepted
consumer-contract hash changed
Hub-FDB rectification required: yes
```

The next operator action is on the separate Hub-FDB rectification surface, not
another FDB mutation merely to update Hubs.


## 23. FDB mutation harness

`fdb_mutate_harness.py` is the standard operator driver for the mutation
lifecycle currently implemented for `add-service` and `remove-service`. The
harness is not an FDB authority and does not infer deployment coordinates on
its own. It drives the public `tools.fdb_control` CLI and verifies every stage.

Normal add invocation:

```text
python fdb_mutate_harness.py add-service --network mainnet --service mainnetc-fdb3
```

Normal remove invocation:

```text
python fdb_mutate_harness.py remove-service --network mainnet --service mainneta-fdb2
```

Without both mutation acknowledgements, the harness stops after `prep` and
prints the resolved host and endpoint:

```text
--execute-mutations
--yes-i-know-this-mutates-fdb
```

With both acknowledgements, the harness drives:

```text
pre-inspect
  -> prep
  -> do
  -> operation-scoped inspect/proof
  -> finalize
  -> final inspect
```

The harness requires the pre-inspection to describe an accepted and independently
verified cluster. It captures the operation ID returned by `prep`, including the
exact frozen target coordinator set and whether that overlay changes, verifies
the operation-specific FDB proof after `do`, and requires the final accepted
generation to advance by exactly one. Final inspection proves the cluster
description is unchanged, the coordinator set equals the frozen target, and the
cluster ID changes exactly when FoundationDB rewrote it for a coordinator change.

For `add-service`, the harness passes only the network and logical service ID to
`prep`; host, address, port, Coolify target, and endpoint are whatever FDB Control
resolved and froze. For `remove-service`, the target host and endpoint are read
from accepted topology.

Every run is recorded under:

```text
runtime/state/fdb/harness-runs/<timestamp>-<pid>/
```

The run directory contains per-step command/stdout/stderr/JSON files and
`harness-state.json`. `--resume <run-dir>` resumes from the last completed step
and reuses the stored operation ID and frozen operator request rather than asking
the operator to reconstruct them.

The harness MUST NOT reconcile drift, choose a replacement operation, invent a
coordinator target, or alter the frozen placement merely to make a mutation
succeed. It may drive the coordinator sub-transition already frozen by FDB
Control as part of the prepared add/remove operation.


## 24. Operator safety rules

1. Inspect before acting and after every ambiguous or interrupted result.
2. Never infer FDB service membership from host count.
3. Never equate coordinator membership with service membership; derive the coordinator overlay from distinct failure domains and freeze it before effects.
4. Never hard-code the required number of hosts, services, coordinators, or
   Hubs into the architecture.
5. Never treat a running container as proof that an FDB service participates in
   the intended logical cluster.
6. Never treat an unreachable service as intentionally removed.
7. Never create a new logical cluster while an existing cluster identity may
   still exist and cannot be distinguished safely.
8. Never remove or replace a service without evaluating the resulting
   host/failure-domain consequences.
9. Never publish container-local-only addresses as the canonical remote Hub
   connection contract.
10. Never mutate Hub services from FDB Control.
11. Never mutate chain services from FDB Control.
12. Never update accepted topology before the operation-specific finalize
    contract is proved.
13. Never claim a mutation complete merely because deployment API calls or
    container starts returned success.
14. Never expose shared-private controller tokens, credentials, or private key
    material in ordinary evidence or consumer contracts.
15. Never rewrite the shared privates file opportunistically from an FDB
    topology operation.
16. Never roll distributed reality backward by assumption. Inspect it and move
    forward through an explicit valid transition.
17. Never use `reconcile` to smuggle in an intended topology change.
18. Never remove a coordinator service until an explicit safe resulting
    coordinator state is established.


## 25. Common evidence and output contract

The exact schema belongs to later functionality/module design, but every staged
mutation MUST preserve enough structured evidence to prove what happened.

The common evidence envelope SHOULD be able to represent at least:

```text
operation ID
operation kind
network / logical cluster identity
operation state
prepared timestamp
execution timestamps
finalized timestamp
accepted prestate identity/hash
observed prestate identity/hash
frozen target
shared private infrastructure bindings used, by non-secret identity only
affected services
affected hosts
affected failure domains
coordinator prestate
coordinator target
coordinator poststate
FDB configuration prestate
FDB configuration target
FDB configuration poststate
observed poststate
verification results
consumer-contract pre-hash
consumer-contract post-hash
consumer contract changed: yes|no
Hub-FDB rectification required: yes|no
unknowns or unresolved conditions
```

Secret bytes MUST NOT be embedded merely to make evidence self-contained.
Evidence may record stable non-secret references to private bindings when needed
for causality.


## 26. Explicitly excluded high-level operations

The initial FDB control surface intentionally does not include these operations:

| Excluded operation | Reason |
|---|---|
| `diagnose` | Part of `inspect` |
| `status` | Part of `inspect` |
| `topology` | Part of `inspect` |
| `verify` | Part of `inspect` and every mutation's finalize contract |
| `plan` | Planning is part of mutation `prep` |
| `deploy` | Ambiguous between new-cluster creation, membership change, and reconciliation |
| `repair` | Too vague; choose the explicit forward transition from observed state |
| `rollback` | Intentionally absent; distributed state moves forward from observed reality |
| `add-machine` | Host placement and service membership are separate concepts |
| `remove-machine` | Host placement and service membership are separate concepts |
| `start-service` | Low-level implementation action beneath an explicit operation such as `reconcile` |
| `stop-service` | Low-level implementation action beneath an explicit topology/lifecycle operation |
| `restart-service` | Low-level implementation action, not an operator topology intent |
| `rectify-hubs` | Separate Hub-FDB rectification control surface |

The operation list grows only when a new operator intent cannot be represented
safely by the existing operations.


## 27. Boundary with the Hub-FDB rectifier

FDB Control ends at a canonical consumer contract plus an explicit answer to:

```text
Did this finalized FDB state change how Hubs must interact with FDB?
```

If no:

```text
consumer contract changed: no
Hub-FDB rectification required: no
```

If yes:

```text
consumer contract changed: yes
Hub-FDB rectification required: yes
```

The separate Hub-FDB rectifier is responsible for:

- discovering Hub instances from Hub authority rather than FDB membership;
- obtaining the current FDB consumer contract;
- determining which Hubs have stale FDB-facing configuration;
- applying the required cluster file/client/API/TLS/namespace configuration;
- restarting or replacing Hub processes only when the Hub-side application
  contract requires it;
- verifying that each rectified Hub can actually use the intended FDB cluster.

FDB Control does not directly perform those actions.


## 28. Relationship to later implementation documents

This document freezes operator meaning, not implementation structure.

The intended design sequence is:

```text
fdb.md
    WHAT the FDB control system is

fdb-o.md
    WHAT the operator can ask it to do and the exact behavioral contracts

fdb-o-f.md
    WHAT reusable functionality is required to satisfy those operations

fdb-o-f-m.md
    WHERE that functionality lives and how modules compose

implementation
```

`fdb-o-f.md` MUST decompose these operation contracts rather than inventing new
operator semantics.

`fdb-o-f-m.md` MUST map those functions into modules without making module
boundaries part of FDB architecture.


## 29. Remaining open items before mutating implementation freeze

The high-level operation set is frozen by `fdb.md`. The following lower-level
contracts still need to be closed during the functionality/module design cycle:

1. exact accepted-topology and operation-record persistence schemas;
2. exact rule and evidence for determining when an FDB service has converged
   sufficiently for `add-service` or `replace-service` finalization;
3. exact transaction/usability probe used by `inspect` and mutation finalization;
4. exact service descriptor schema beyond the now-frozen normal add-service
   placement/address/port allocator;
5. exact consumer-contract schema, canonical serialization, and generation rule;
6. exact FDB-client/server compatibility representation;
7. exact TLS/transport material references when TLS is enabled;
8. exact FDB-native settings initially supported by `configure-cluster`;
9. explicit incomplete-operation supersession bookkeeping for forward-only
   recovery when the original frozen target is abandoned;
10. exact retirement data-retention versus destructive-volume policy;
11. exact relationship between one FDB `service` and one or more FDB runtime
    processes in the deployment implementation.

The shared-privates host mapping and the normal `add-service` placement surface
are no longer open: section 10.3 freezes them.

None of these open items permits the implementation to weaken the operator
boundaries defined above.
