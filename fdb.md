# FDB control surface

Status: high-level design baseline for the FoundationDB control surface. This document defines the operator-visible system boundary and the high-level operations that FDB Control needs before lower-level command contracts are implemented.

## Purpose

FoundationDB is an independently operated distributed substrate in Main Computer. It is not part of a Besu validator and it is not part of a Hub instance.

The chain and FoundationDB are peer substrate systems. The Hub depends on both:

```text
             HUB POOL
             /      \
            /        \
           v          v
        CHAIN        FDB
```

The three systems have independent lifecycle, topology, cardinality, placement, scaling, and failure behavior.

Physical co-location does not create lifecycle ownership. One physical host can run a Besu service, one or more FDB services, and a Hub service while those services remain members of separate systems governed by separate control surfaces.

## Control-surface boundary

FDB Control owns FoundationDB state and FoundationDB lifecycle operations.

FDB Control does not own:

- Besu or validator lifecycle;
- Hub creation, removal, replacement, or scaling;
- Hub chain-facing configuration;
- Hub FDB-facing reconciliation.

The Hub-FDB dependency is handled by a separate operator-runnable rectification script. FDB Control produces the authoritative FDB consumer contract. The Hub-FDB rectifier consumes that contract and updates the Hubs when the FDB change alters how the Hubs need to interact with FoundationDB.

The intended control relationship is:

```text
FDB Control
    |
    | owns and changes FDB
    v
FDB cluster
    |
    | publishes current consumer contract
    v
Hub-FDB rectifier
    |
    | applies required Hub-facing FDB configuration
    v
Hub pool
```

FDB Control does not directly rewrite or redeploy Hub services merely because FDB changed.

## Topology model

FDB Control distinguishes the logical cluster, physical hosts, FDB services, coordinators, and failure domains.

```text
FDB cluster
  |
  +-- host A
  |     +-- service A1
  |     +-- service A2
  |
  +-- host B
  |     +-- service B1
  |
  +-- host C
        +-- service C1
        +-- service C2
```

A host is a placement target and a physical failure domain. An FDB service is an independently managed FDB runtime service placed on a host.

FDB Control must allow multiple FDB services on the same host. Service count and independent host/failure-domain count are therefore different properties and must never be treated as equivalent.

Coordinator membership is also separate from service membership. A service can exist without being a coordinator. The normal service-topology operations derive a coordinator quorum overlay from the resulting accepted services and their physical failure domains; they do not make every service a coordinator or equate service count with coordinator count.

No architectural count is hard-coded. Mainnet, testnet, development networks, and future networks can use different numbers of hosts, services, coordinators, or Hubs. Counts are deployment policy and observed topology, not constants in the control model.

## Operating rule: move forward from observed state

FDB Control does not expose rollback as a high-level operation.

After a partial or failed distributed mutation, the control surface first establishes the current observed state and then chooses an explicit safe forward transition. It does not pretend that the previous distributed state can always be reconstructed exactly.

The normal response to an interrupted operation is therefore:

```text
inspect current state
    -> identify the valid intended transition
    -> reconcile or perform an explicit topology operation
    -> verify convergence
    -> record evidence
```

A generic `repair` operation is not part of the high-level surface. If a future failure class requires a distinct operator intent that cannot be expressed safely by the existing operations, that operation can be added when the concrete requirement exists.

## High-level operations

FDB Control has ten high-level operations:

```text
fdb-control inspect

fdb-control create-cluster
fdb-control add-service
fdb-control remove-service
fdb-control replace-service

fdb-control set-coordinators
fdb-control configure-cluster

fdb-control reconcile
fdb-control consumer-contract

fdb-control retire-cluster
```

These names describe operator intent. Lower-level stages such as discovery, preflight, execution, convergence checks, verification, and evidence writing are implementation phases beneath these operations rather than additional high-level commands.

### `inspect`

`inspect` is the complete read-only operator view of the FDB cluster.

It discovers and verifies the current state rather than merely printing configuration. At minimum, it should establish:

- cluster identity;
- accepted topology;
- observed topology;
- physical hosts and failure domains;
- FDB services and their host placement;
- reachable and unreachable services;
- coordinator membership and coordinator reachability;
- whether coordinator majority is satisfied;
- current FDB database configuration;
- redundancy state;
- database availability;
- recovery or degraded state when exposed by FoundationDB;
- remaining failure tolerance when it can be established reliably;
- whether expected membership and observed membership differ;
- current FDB consumer-contract identity or hash;
- whether the Hub-facing FDB dependency has changed or is stale;
- any unknown state that prevents a safe conclusion.

`inspect` includes verification. There is no separate high-level `verify`, `diagnose`, `status`, or `topology` operation. Those are facets of inspection and can become output modes or internal functions if useful.

`inspect` must not mutate FoundationDB, deployment state, Hub state, or accepted authority merely by observing them.

### `create-cluster`

`create-cluster` establishes a new logical FoundationDB cluster lineage.

It creates the initial cluster identity, initial service placement, initial coordinator set, and initial FoundationDB configuration. It verifies that the resulting cluster is usable and records the first accepted topology and consumer contract.

`create-cluster` is not a generic deploy command. It must distinguish creation of a new cluster from reconstruction or reconciliation of an already existing cluster. It must not silently recreate an existing database as though it were empty.

### `add-service`

`add-service` adds one FDB service to an existing cluster at an explicitly selected host placement.

It must support adding a service to a host that already runs another FDB service. For example, both of these are valid topology changes:

```text
host A: A1 -> A1, A2
```

and:

```text
host D: no FDB service -> D1
```

The operation verifies that the new service joined the intended cluster and converged sufficiently for the operation to be accepted.

During `prep`, `add-service` derives and freezes the safe coordinator overlay for the resulting failure-domain topology. If the derived set differs, `do` first proves the new service participates through the source coordinator set, then moves coordinator authority to the frozen target, proves the rewritten connection information, and finalizes both changes as one service-topology operation. Adding another service in an already represented failure domain does not by itself create another coordinator.

### `remove-service`

`remove-service` intentionally removes one identified FDB service from the accepted cluster topology.

Before mutation, it evaluates the effect of removing that service on database availability, configured redundancy, coordinator state, and physical failure-domain tolerance. A service on a host containing other FDB services can be removed without implying removal of those sibling services or the host itself.

If the target service is a coordinator, `remove-service` derives and freezes the coordinator overlay for the surviving failure-domain topology during `prep`. `do` moves and proves coordinator authority before exclusion, drain, or deletion of the target. The operator does not need to run a separate coordinator command merely to retire a coordinator service safely.

### `replace-service`

`replace-service` expresses the operator intent that one existing or failed FDB service is being replaced by another service.

The replacement can be placed on the same host or on a different host. The operation preserves the causal relationship between the old service and its replacement instead of reducing the intent to two unrelated commands.

Internally, replacement can use safe add, convergence, coordinator, and removal steps as appropriate, but the high-level operation remains one replacement transition with one verified postcondition.

### `set-coordinators`

`set-coordinators` is the explicit advanced override for intentionally choosing a coordinator set independently of a service-membership change.

Normal add/remove/replace operations derive their coordinator overlay from the resulting accepted services and distinct failure domains. `set-coordinators` exists when the operator intentionally wants a different valid set without changing membership. It never means that all services become coordinators, and it does not replace the topology-derived coordinator sub-transition used by ordinary membership operations.

The operation verifies that the requested coordinators are valid and reachable through the network topology required by the cluster, performs the supported FoundationDB coordinator transition, observes convergence, and derives the resulting Hub-facing consumer contract.

Because coordinator changes can alter the FDB cluster connection information used by remote Hub clients, completion must report whether the consumer contract changed and whether Hub-FDB rectification is required.

### `configure-cluster`

`configure-cluster` changes FoundationDB's own logical database configuration.

This includes configuration that belongs to FoundationDB itself, such as supported redundancy or storage-policy settings. It does not serve as a generic place for unrelated Docker, Coolify, Hub, chain, or host configuration.

The operation verifies that the requested configuration is compatible with the observed topology before applying it and proves the post-change cluster state afterward.

### `reconcile`

`reconcile` restores the already accepted FDB topology when runtime or deployment reality has drifted without an intentional topology change.

For example:

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

If `C1` is still an accepted service and no operation intentionally retired or replaced it, `reconcile` can restore the required service and verify that it rejoins the existing cluster.

`reconcile` does not invent new membership. If the intended topology is actually changing, the operator uses `add-service`, `remove-service`, `replace-service`, `set-coordinators`, or `configure-cluster` as appropriate.

### `consumer-contract`

`consumer-contract` derives the authoritative FDB configuration required by the Hub-FDB rectification layer.

The exact schema can be refined during implementation, but the contract needs to capture the Hub-relevant facts required to connect to and use the current FDB cluster correctly. Depending on the final implementation, these can include:

- FDB cluster identity;
- current cluster connection information;
- coordinator/bootstrap addresses that are reachable from Hub hosts;
- FDB client/server compatibility requirements;
- FDB API-version requirements;
- transport or TLS requirements when used;
- Hub storage namespace inputs that belong at this dependency boundary;
- consumer-contract generation or equivalent identity;
- deterministic contract hash;
- enough metadata to determine whether a newer FDB state changes Hub behavior.

The consumer contract describes the FDB dependency. It does not mutate the Hubs.

When an FDB topology change does not change the consumer contract, Hub-FDB rectification is unnecessary solely because that topology changed. When the consumer contract does change, FDB Control reports that fact and the operator can run the Hub-FDB rectifier.

### `retire-cluster`

`retire-cluster` deliberately decommissions an entire logical FDB cluster lineage.

It is distinct from removing an individual service. The cluster must never be considered retired merely because its services are temporarily unavailable, missing from deployment state, or reduced to an empty observed runtime.

Retirement requires explicit operator intent and evidence sufficient to distinguish intentional decommissioning from failure or drift.

## Common mutation lifecycle

Every mutating high-level operation follows the same internal discipline:

```text
discover current state
    -> determine the exact requested transition
    -> prove preconditions and safety constraints
    -> execute the bounded mutation
    -> observe FoundationDB convergence
    -> verify the requested postcondition
    -> derive any consumer-contract change
    -> record operation evidence
```

These stages are not separate high-level operations. They are the implementation contract underneath `create-cluster`, `add-service`, `remove-service`, `replace-service`, `set-coordinators`, `configure-cluster`, `reconcile`, and `retire-cluster`.

A failed stage leaves the system to be understood from its new observed state. The next action is selected from that state rather than by invoking a generic rollback command.

## Hub-FDB rectification boundary

FDB mutations and Hub rectification are intentionally separate operations.

An FDB operation can finish with one of these conclusions:

```text
consumer contract changed: no
Hub-FDB rectification required: no
```

or:

```text
consumer contract changed: yes
Hub-FDB rectification required: yes
```

When rectification is required, the separate Hub-FDB rectifier script consumes the current FDB consumer contract, inspects the Hub pool, determines which Hub instances have stale or incorrect FDB-facing configuration, applies the required configuration to those Hubs, and verifies that the selected Hubs can use the FDB cluster.

This preserves the lifecycle boundary:

```text
FDB Control owns FDB.
Hub-FDB rectification owns the FDB -> Hub dependency boundary.
Hub Control owns Hub lifecycle.
```

## Explicitly excluded high-level operations

The initial control surface does not include the following separate high-level operations:

- `deploy`: ambiguous between cluster creation, service addition, and reconciliation;
- `verify`: verification is part of `inspect` and every mutation;
- `diagnose`: diagnosis is part of `inspect`;
- `status`: status is part of `inspect`;
- `topology`: topology is part of `inspect`;
- `rollback`: distributed state moves forward from the observed state;
- `repair`: too vague until a concrete failure class requires a distinct operation;
- `add-machine` / `remove-machine`: host placement and FDB service membership are separate concepts, and multiple FDB services can share one host.

The operation list can grow only when a new operator intent cannot be represented safely and clearly by the existing operations.

## High-level invariant

The FDB control surface is correct at the high level when an operator can answer and perform all of the following without hard-coded topology assumptions:

```text
What FDB cluster exists right now?
Is it usable and what is degraded?
Which hosts and services constitute it?
Which services are coordinators?
Can I create, add, remove, replace, or reconcile services safely?
Can I explicitly change coordinators or FDB configuration?
What FDB contract do the Hubs currently need?
Did an FDB change require Hub-FDB rectification?
Can I intentionally retire the cluster without confusing retirement with failure?
```

Everything below this document should preserve those operator meanings rather than exposing implementation details as new architecture.
