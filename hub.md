# Hub control surface

Status: architecture baseline; first-Hub `unborn -> 1` lifecycle slice implemented.

## Purpose

The Hub is a consumer-facing runtime that depends on two independently operated substrate systems: the chain and FoundationDB.

```text
CHAIN ------------------\
                         > HUB
FDB --------------------/
```

Hub Control owns Hub membership and placement. It does not own either dependency.

The effective Hub runtime projection is:

```text
accepted Hub topology
+ accepted FDB consumer contract
+ accepted Chain consumer contract
= Hub runtime projection
```

A change to one source does not transfer ownership of another source. FDB changes do not authorize Hub membership mutation. Chain changes do not authorize Hub membership mutation. Hub lifecycle changes do not authorize FDB or chain mutation.

## Authority model

Hub Control owns:

- which Hub identities are accepted;
- where each accepted Hub is placed;
- accepted Hub generation;
- birth, addition, removal, and terminal deletion of Hub membership;
- proving the deployed Hub matches the frozen lifecycle target;
- recording which dependency contracts were installed into a newly created Hub.

Hub Control does not own:

- FDB service membership or coordinators;
- FDB cluster-file truth or namespace authority;
- Besu validator or RPC topology;
- chain contract deployment identities;
- the canonical FDB consumer contract;
- the canonical Chain consumer contract;
- rectifying already-accepted Hubs after a dependency contract changes.

The two dependency edges have separate rectification authorities documented in `hub-fdb*.md` and `hub-chain*.md`.

## Accepted topology

Accepted Hub state is deliberately small. It records Hub membership and placement plus references to consumed dependency contracts. It must not become a second authority for dependency internals.

Conceptually:

```json
{
  "schema": "main-computer.hub-accepted.v1",
  "network": "mainnet",
  "generation": 4,
  "hubs": [
    {"hub_id": "mainneta-hub1", "host_id": "coolify-a"},
    {"hub_id": "mainnetc-hub1", "host_id": "coolify-c"}
  ],
  "fdb_contract": {"generation": 8, "sha256": "..."},
  "chain_contract": {"generation": 12, "sha256": "..."}
}
```

Copied coordinator lists, copied RPC URLs, copied deployed-contract addresses, Coolify UUIDs, and runtime file paths are evidence or runtime projection, not accepted Hub topology authority.

## Lifecycle

The lifecycle is cardinality-independent:

```text
0 -> 1     ordinary add-hub rebirth
N -> N+1   ordinary add-hub
N -> N-1   ordinary remove-hub
1 -> 0     remove-hub with explicit --allow-full-deletion acknowledgement
```

`0 -> 1` requires no special rebirth command. `add-hub` is the operator intent; the harness determines whether the transition is a rebirth.

`1 -> 0` is intentionally destructive and requires a separate acknowledgement in addition to ordinary mutation authorization.

## First live lifecycle slice

The first implemented mutation is deliberately vertical rather than broad:

```text
unborn
  + add-hub <hub-id>
  -> resolve placement
  -> freeze current FDB consumer contract
  -> freeze and validate current Chain consumer contract
  -> deploy one Hub
  -> prove Hub identity + FDB-backed status + exact chain projection
  -> publish accepted Hub generation 1
```

An unborn Hub layer is therefore a valid `add-hub` prestate with virtual starting generation `0`. No `create-hub` command or rebirth flag exists. The first accepted state is generation `1`.

This first slice also relaxes stable Hub topology validation to permit one concrete Hub. Multi-Hub topology remains valid; one-Hub topology is required for birth.

## Dependency rule at birth

A newly added Hub must be built from the current accepted dependency contracts. Birth therefore proves all of the following before accepted Hub authority advances:

```text
Hub deployment exists
Hub process is healthy
Hub identity/placement matches the frozen target
current FDB consumer contract is installed and usable
current Chain consumer contract is installed and usable
```

A running container alone is not proof of a valid Hub.

## Rectification boundary

Once a Hub is accepted, later dependency changes are not Hub membership changes.

```text
FDB contract changes   -> Hub-FDB rectifier
Chain contract changes -> Hub-Chain rectifier
```

The rectifiers may change dependency-facing Hub runtime projection but may not add or remove Hubs.

## Shared private infrastructure

Hub Control may read the canonical private infrastructure source needed to reach deployment targets. That source supplies physical/controller facts and credentials. It is not Hub membership authority.

The current legacy private/network material also contains Hub and dependency values mixed together. Those fields are migration evidence only once canonical Hub accepted state and consumer contracts exist.

## Legacy implementation evidence

The existing Hub application/runtime code is implementation evidence and should be reused where it satisfies this authority model. In particular, useful behavior exists in:

```text
tools/coolify_hub_service.py
tools/coolify_hub_cluster.py
main_computer/hub.py
main_computer/stable_hub.py
main_computer/stable_hub_topology.py
```

Those files do not define the new authority boundary. The current combined Hub/FDB placement model and manually copied dependency values are to be demoted rather than perpetuated.

## Forward-only operating rule

After a partial mutation, Hub Control first establishes observed state and then continues or chooses a safe forward action. It does not promise that a distributed deployment can always be rolled back byte-for-byte.

The normal mutation protocol is:

```text
pre-inspect -> prep -> mutation gate -> do -> operation-inspect -> finalize -> final-inspect
```

Only the repository-level harness exposes operator intent. Internal stages and operation IDs are implementation details.
