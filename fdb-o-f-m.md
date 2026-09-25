# FDB operation-functionality-module specification

Status: module-contract companion to `fdb-o-f.md`

Sources reviewed:

```text
fdb.md SHA-256: fe0dd9fcbe702c3d7b04bbb6a8ba2072f59e4a5f6de300d735d929561d39df1f
fdb-o.md SHA-256: 0f45df125ce6ea6a8600c6830697094b77a0e5cc7a578f55b11b55df97bc551b
fdb-o-f.md SHA-256: bc79d49cb1e5aa2e8dbbe57265f991fbc6c098551e3db1b3d0e52095f306df0a
```

Repository implementation evidence reviewed:

```text
tools/coolify_fdb_cluster.py
tools/coolify_hub_service.py (transport patterns only; not an FDB authority)
tools/mother/common/* (module-structure reference only; FDB Control does not import Mother authority)
```

## 1. Purpose and authority

This document closes the operation × functionality × module layer for FDB Control. It maps every non-gap `FDB-OF-*` functionality in `fdb-o-f.md` to a concrete module and public seam while preserving the operator meaning fixed by `fdb.md` and `fdb-o.md`.

The authority chain is:

```text
fdb.md
  -> fdb-o.md
    -> fdb-o-f.md
      -> fdb-o-f-m.md
        -> traced contract tests
          -> implementation
```

If this document conflicts with a higher-level source, the higher-level source governs. Module convenience never changes an operation, stage, safety precondition, accepted/observed distinction, service/host distinction, forward-only lifecycle rule, or Hub-FDB authority boundary.

This document intentionally freezes package/module ownership and public module seams. It does not silently close a `contract-open` parent gap. A module whose required behavior depends on an open parent contract MUST fail before the first lock, durable write, deployment mutation, FDB mutation, or other external side effect with the exact `FDB_OPEN_*` error named in section 16.

## 2. Module decomposition rules

### 2.1 Module boundary

A module is an implementation ownership boundary. Each module owns one coherent responsibility, exposes typed public calls, and has one declared authority class. A module may implement several functionalities when they share the same authority and external-effect boundary. A functionality may call several modules in an ordered chain when the decomposition is explicit in section 7.

A module is not permitted to become a hidden deploy script. In particular, no module may both reinterpret operator intent and perform live mutation. Intent is frozen above live adapters.

### 2.2 No Mother lifecycle dependency

FDB Control and Mother share the same private infrastructure input, but FDB Control is not a child of Mother. The code dependency rule is therefore stronger than the architectural statement:

```text
tools.fdb_control may read runtime/state/mother/identity.private.yaml as shared infrastructure input
tools.fdb_control MUST NOT import tools.mother.*
tools.fdb_control MUST NOT invoke Mother commands
tools.fdb_control MUST NOT read other runtime/state/mother/* files or treat identity.private.yaml as FDB topology authority
```

If a genuinely generic infrastructure helper is later extracted into a neutral package, Mother and FDB Control may both depend on that neutral helper. Until then, FDB Control owns its own narrow parser/adapters for the shared private file rather than importing Mother control-surface code.

### 2.3 No Hub lifecycle dependency

FDB Control produces the FDB consumer contract and reports whether Hub-FDB rectification is required. It does not enumerate, restart, redeploy, or rewrite Hub services. The new FDB package MUST NOT import `coolify_hub_service.py` as an orchestration dependency. Generic HTTP/Coolify transport logic may be migrated into the FDB adapter, but Hub service lifecycle behavior remains outside this package.

### 2.4 Module authority classes

| Class | Meaning |
|---|---|
| `core-pure` | deterministic validation/derivation only; no filesystem/network/process effects |
| `reader` | reads filesystem, deployment APIs, network, or FDB but performs no authoritative or live mutation |
| `derived-writer` | writes reports/evidence/consumer artifacts that do not by themselves change accepted FDB authority |
| `state-writer` | writes FDB Control operation/scope state, not live FDB |
| `accepted-authority-writer` | sole writer of accepted FDB topology/configuration/retirement authority |
| `live-adapter` | sole boundary to a specific external mutating/read transport such as Coolify, `fdbcli`, or the Python FDB client |
| `protocol` | composes typed lower-level seams to implement one functional protocol; may request effects only through adapters/writers |
| `orchestrator` | operation entry point; composes declared functionality chains and writes nothing directly |

### 2.5 Dependency direction

```text
operation entry modules
  -> protocol/control modules
    -> state modules + live/read adapters
      -> core modules
```

Rules:

- core modules import only Python standard-library code and other explicitly lower core modules;
- adapters may import vendor libraries but never operation modules;
- state modules may import core modules but not operation modules;
- protocol modules may import core, state, reader, and adapter modules;
- operation modules may import declared lower layers;
- circular imports are prohibited;
- no lower layer may call back into an operation module;
- no FDB Control module may import Mother operation/state authority.

### 2.6 Composition semantics

An ordered module chain means each public call returns a validated typed result that becomes the next call's input. The caller MUST NOT reread mutable external state and substitute that reread for the returned result unless the functionality explicitly requires a fresh observation. `unknown` and `ambiguous` are first-class results and are never translated into success.

Every live mutation is dispatched through exactly one declared live adapter and then independently verified through a read path. A deployment API success is not FDB participation proof; FDB participation proof is not accepted-topology authority.

## 3. Target package and command surface

### 3.1 Package layout

The target package is deliberately named `fdb_control`, not `fdb`, so it cannot shadow the FoundationDB Python package named `fdb`.

```text
tools/fdb_control/
  __init__.py
  __main__.py
  inspect.py
  consumer_contract.py
  create_cluster.py
  add_service.py
  remove_service.py
  replace_service.py
  set_coordinators.py
  configure_cluster.py
  reconcile.py
  retire_cluster.py
  common/
    __init__.py
    models.py
    errors.py
    canonical.py
    hashing.py
    paths.py
    atomic_files.py
    privates.py
    host_bindings.py
    accepted_state.py
    operation_store.py
    scopes.py
    contract_state.py
    intent.py
    planning.py
    operation_control.py
    deployment_inventory.py
    fdb_status.py
    drift.py
    transaction_probe.py
    network.py
    coolify.py
    service_descriptors.py
    service_placement.py
    service_lifecycle.py
    cluster_file.py
    fdbcli.py
    fdb_client.py
    cluster_bootstrap.py
    participation.py
    convergence.py
    evacuation.py
    coordinators.py
    configuration.py
    consumer_contract.py
    evidence.py
    reporting.py
```

### 3.2 Canonical invocation

The implementation entry point is:

```text
python -m tools.fdb_control
```

`fdb-control` remains documentation shorthand. This closes `FDB-OF-GAP-016` for the Python/module path and parser ownership. Normal add-service target spelling is now closed under `FDB-OF-GAP-017`; composite target syntax for other operations and final human/JSON presentation remain surface-open.

The parser recognizes exactly the ten high-level operations from `fdb-o.md`:

```text
inspect
consumer-contract
create-cluster
add-service
remove-service
replace-service
set-coordinators
configure-cluster
reconcile
retire-cluster
```

Read-only operations are one-shot. Mutating operations use `prep`, `do`, and `finalize`. There is no `rollback` parser branch.

### 3.3 Operator mutation harness

The repository-level `fdb_mutate_harness.py` drives the canonical CLI for the
implemented service mutations:

```text
python fdb_mutate_harness.py add-service --network <network> --service <service-id>
python fdb_mutate_harness.py remove-service --network <network> --service <service-id>
```

It is not imported by `tools.fdb_control` and owns no direct deployment or FDB
mutation seam.


## 4. Shared type and error contract

### 4.1 Public typed values

`FDB-OFM-CORE-001` owns immutable, frozen, slot-based dataclasses used across module boundaries. The minimum public type set is:

```text
FdbContext
ContentHash
OperationIdentity
HostBindingRef
ServicePlacement
CoordinatorEndpoint
ClusterIdentity
AcceptedClusterState
ServiceObservation
DeploymentObservation
FdbStatusObservation
CoordinatorObservation
RedundancyObservation
FailureDomainSummary
FailureToleranceConclusion
DriftReport
FrozenTarget
OperationPlan
PreparedOperation
OperationProgress
ConsumerContractDraft
ConsumerContractArtifact
EvidenceRef
InspectionResult
OperationCommandResult
```

The domain rules for these values are normative even where a persistent wire schema is still open:

- service identity and host identity are separate fields;
- many service placements may reference the same host;
- coordinator endpoints are a distinct quorum overlay, not all services; normal membership operations derive them from distinct failure domains and freeze the exact target before effects;
- accepted state and observed state use different types;
- a running deployment resource does not satisfy a field that means FDB participation;
- secret bytes are never members of ordinary evidence/report/consumer-contract models;
- all tuples used for canonical sets are sorted by UTF-8 byte ordering of their stable identity keys before hashing/serialization;
- boolean values are not accepted where integer counts are required;
- network/cluster/service/host IDs are non-empty and validated before use in a path or external request.

The exact persistent schema for `AcceptedClusterState`, prepared-operation records, and progress records remains blocked by `FDB-OF-GAP-012`; the exact external consumer-contract wire object remains blocked by `FDB-OF-GAP-015`. In-memory types may be implemented for read-only work, but a blocked persistence/publication seam cannot claim durable conformance until the parent contract closes.

### 4.2 FDB context

`FdbContext` carries resolved process-local dependencies and immutable roots. At minimum it provides: repo root, FDB state root, shared-private path, clock/timestamp provider, and adapter factories or injected test doubles. It does not contain Mother topology, Mother journals, Besu state, or Hub membership.

Default roots are:

```text
repo root:               repository root
shared private input:    runtime/state/mother/identity.private.yaml
FDB control state root:  runtime/state/fdb
network state root:      runtime/state/fdb/<network>/
```

### 4.3 Error envelope

All public module errors derive from `FdbControlError` and carry at least:

```text
code
message
module_id
operation_id or null
retry_class: never | exact-retry | inspect-first | contract-open
effect_class: none | derived-local | control-state | live-deployment | live-fdb | accepted-authority
```

An error never claims that an external effect did not occur merely because the local request failed. Ambiguous external outcomes return `inspect-first` and retain the same operation/request identity.

### 4.4 Contract-open errors

Contract-open calls fail before their first effect with the exact code listed in section 16. These are positive contract tests, not `xfail` placeholders.

## 5. Stable module registry

Every module ID below maps to exactly one source file. Public API names are normative seams; internal helper names are not.

| Module ID | Path | Authority | Public responsibility |
|---|---|---|---|
| `FDB-OFM-APP-001` | `tools/fdb_control/__main__.py` | `orchestrator` | `main(argv=None) -> int`; canonical CLI registry and exit-code mapping; no domain logic |
| `FDB-OFM-APP-002` | `tools/fdb_control/inspect.py` | `orchestrator` | `run(ctx, request) -> InspectionResult`; complete read-only inspection pipeline |
| `FDB-OFM-APP-003` | `tools/fdb_control/consumer_contract.py` | `orchestrator` | `run(ctx, request) -> ConsumerContractCommandResult`; one-shot FDB→Hub dependency derivation |
| `FDB-OFM-APP-004` | `tools/fdb_control/create_cluster.py` | `orchestrator` | `prep`, `do`, `finalize`; new logical FDB lineage birth |
| `FDB-OFM-APP-005` | `tools/fdb_control/add_service.py` | `orchestrator` | `prep`, `do`, `finalize`; add one service independently of host count |
| `FDB-OFM-APP-006` | `tools/fdb_control/remove_service.py` | `orchestrator` | `prep`, `do`, `finalize`; remove one accepted service with explicit safety proof |
| `FDB-OFM-APP-007` | `tools/fdb_control/replace_service.py` | `orchestrator` | `prep`, `do`, `finalize`; causal service replacement |
| `FDB-OFM-APP-008` | `tools/fdb_control/set_coordinators.py` | `orchestrator` | `prep`, `do`, `finalize`; explicit coordinator-set mutation |
| `FDB-OFM-APP-009` | `tools/fdb_control/configure_cluster.py` | `orchestrator` | `prep`, `do`, `finalize`; FDB-native configuration mutation |
| `FDB-OFM-APP-010` | `tools/fdb_control/reconcile.py` | `orchestrator` | `prep`, `do`, `finalize`; restore live state to unchanged accepted intent |
| `FDB-OFM-APP-011` | `tools/fdb_control/retire_cluster.py` | `orchestrator` | `prep`, `do`, `finalize`; explicit lineage retirement |
| `FDB-OFM-HARNESS-001` | `fdb_mutate_harness.py` | `operator-driver` | drive public add/remove mutation lifecycle, mutation gate, per-step run capture, resume, and final topology/generation assertions; no direct infrastructure authority |
| `FDB-OFM-CORE-001` | `tools/fdb_control/common/models.py` | `core-pure` | immutable typed values crossing all public seams |
| `FDB-OFM-CORE-002` | `tools/fdb_control/common/errors.py` | `core-pure` | `FdbControlError` envelope and stable error namespaces |
| `FDB-OFM-CORE-003` | `tools/fdb_control/common/canonical.py` | `core-pure` | canonical JSON/text encoding and deterministic ordering |
| `FDB-OFM-CORE-004` | `tools/fdb_control/common/hashing.py` | `core-pure` | SHA-256 content hashing for state/evidence/contract identity |
| `FDB-OFM-CORE-005` | `tools/fdb_control/common/paths.py` | `core-pure` | repo/state-root path construction, containment, and reserved namespaces |
| `FDB-OFM-CORE-006` | `tools/fdb_control/common/atomic_files.py` | `state-writer` | same-filesystem atomic file publication helper; no domain authority decisions |
| `FDB-OFM-PRIV-001` | `tools/fdb_control/common/privates.py` | `reader` | read/validate shared `runtime/state/mother/identity.private.yaml`; never writes it |
| `FDB-OFM-PRIV-002` | `tools/fdb_control/common/privates.py` | `reader` | resolve non-secret Coolify host bindings and the FDB-routable host address from the frozen private-address priority rule |
| `FDB-OFM-STATE-001` | `tools/fdb_control/common/accepted_state.py` | `accepted-authority-writer` | accepted cluster/topology/configuration/retirement owner |
| `FDB-OFM-STATE-002` | `tools/fdb_control/common/operation_store.py` | `state-writer` | prepared operation, progress, retry identity, terminal state owner |
| `FDB-OFM-STATE-003` | `tools/fdb_control/common/scopes.py` | `state-writer` | logical per-cluster mutation ownership/conflict control |
| `FDB-OFM-STATE-004` | `tools/fdb_control/common/contract_state.py` | `derived-writer` | consumer-contract generation/artifact history owner; never mutates Hubs |
| `FDB-OFM-CTL-001` | `tools/fdb_control/common/intent.py` | `core-pure` | parse operation-specific typed intent from already parsed CLI values |
| `FDB-OFM-CTL-002` | `tools/fdb_control/common/planning.py` | `protocol` | freeze targets, calculate dependency order, and produce immutable operation plans |
| `FDB-OFM-CTL-003` | `tools/fdb_control/common/operation_control.py` | `protocol` | revalidation, allowed-next-action, interruption classification, forward-only lifecycle control |
| `FDB-OFM-OBS-001` | `tools/fdb_control/common/deployment_inventory.py` | `reader` | inventory FDB deployment resources/runtime identity through declared deployment adapter |
| `FDB-OFM-OBS-002` | `tools/fdb_control/common/fdb_status.py` | `reader` | normalize live FoundationDB status, coordinators, redundancy, and recovery facts |
| `FDB-OFM-OBS-003` | `tools/fdb_control/common/drift.py` | `core-pure` | accepted-vs-observed drift and supported failure-tolerance conclusions |
| `FDB-OFM-OBS-004` | `tools/fdb_control/common/transaction_probe.py` | `reader` | bounded database usability transaction probe; contract-open until parent closes probe semantics |
| `FDB-OFM-NET-001` | `tools/fdb_control/common/network.py` | `reader` | address validation and bounded reachability probes; transport/TLS subpath remains contract-open |
| `FDB-OFM-DEPLOY-001` | `tools/fdb_control/common/coolify.py` | `live-adapter` | sole Coolify API transport/context/service lookup adapter for FDB Control |
| `FDB-OFM-DEPLOY-002` | `tools/fdb_control/common/service_descriptors.py` | `core-pure` | render exact per-service deployment descriptor from frozen placement intent |
| `FDB-OFM-DEPLOY-004` | `tools/fdb_control/common/service_placement.py` | `core-pure` | parse normal add-service identity, resolve placement token against the network Coolify controller registry, and allocate the first unused accepted FDB port on the resolved controller placement |
| `FDB-OFM-DEPLOY-003` | `tools/fdb_control/common/service_lifecycle.py` | `protocol` | create/update/deploy/verify/stop/remove/restore one FDB service via deployment adapter |
| `FDB-OFM-FDB-001` | `tools/fdb_control/common/cluster_file.py` | `core-pure` | parse/render FoundationDB cluster connection seed/contents from explicit cluster identity and coordinator endpoints |
| `FDB-OFM-FDB-002` | `tools/fdb_control/common/fdbcli.py` | `live-adapter` | sole subprocess adapter for bounded `fdbcli` execution against an explicit cluster file |
| `FDB-OFM-FDB-003` | `tools/fdb_control/common/fdb_client.py` | `live-adapter` | sole Python FoundationDB client adapter for bounded transactional probes when enabled |
| `FDB-OFM-FDB-004` | `tools/fdb_control/common/cluster_bootstrap.py` | `protocol` | new-cluster identity/bootstrap/configure orchestration; birth only |
| `FDB-OFM-FDB-005` | `tools/fdb_control/common/participation.py` | `protocol` | attach/verify service participation and detect partial/stale participation |
| `FDB-OFM-FDB-006` | `tools/fdb_control/common/convergence.py` | `protocol` | membership/configuration convergence and redundancy proof; proof thresholds remain contract-open |
| `FDB-OFM-FDB-007` | `tools/fdb_control/common/evacuation.py` | `protocol` | safe service evacuation/exclusion/withdrawal; mutation path contract-open |
| `FDB-OFM-COORD-001` | `tools/fdb_control/common/coordinators.py` | `core-pure` | resolve explicit coordinator endpoints, derive topology coordinator overlays from distinct `zone_id` failure domains, and compare frozen sets |
| `FDB-OFM-COORD-002` | `tools/fdb_control/common/coordinator_runtime.py` | `protocol` | apply a frozen coordinator transition, capture the actual rewritten cluster connection string, and maintain the deterministic coordinator guardian proof surface |
| `FDB-OFM-CFG-001` | `tools/fdb_control/common/configuration.py` | `protocol` | validate/apply/verify supported FDB-native configuration; mutating set contract-open |
| `FDB-OFM-CON-001` | `tools/fdb_control/common/consumer_contract.py` | `protocol` | derive, compare, canonicalize, and classify FDB→Hub consumer contract; open fields remain gated |
| `FDB-OFM-EV-001` | `tools/fdb_control/common/evidence.py` | `derived-writer` | redacted immutable operation/inspection evidence and hashes |
| `FDB-OFM-EV-002` | `tools/fdb_control/common/reporting.py` | `derived-writer` | human-readable and JSON results from the same typed verified facts |

### 5.1 Core public APIs

The following minimum public seams are required. Signatures are Python-like contracts; implementations may add keyword-only dependency injection that does not alter semantics.

```python
# FDB-OFM-CORE-003 canonical.py
def canonical_json(value: object) -> bytes: ...

# FDB-OFM-CORE-004 hashing.py
def sha256_bytes(payload: bytes) -> ContentHash: ...

# FDB-OFM-CORE-005 paths.py
def network_paths(ctx: FdbContext, network: str) -> object: ...
def require_contained(path: Path, root: Path) -> Path: ...

# FDB-OFM-PRIV-001 / FDB-OFM-PRIV-002 privates.py
def load_private_infrastructure(ctx: FdbContext) -> object: ...
def resolve_host_binding(private_doc: object, host_id: str, *, base_dir: Path | None = None) -> HostBindingRef: ...
def resolve_host_fdb_address(private_doc: object, host_id: str) -> tuple[str, str]: ...
def public_binding_ref(binding: HostBindingRef) -> object: ...

# FDB-OFM-DEPLOY-004 service_placement.py
def parse_service_identity(network: str, service_id: str) -> ParsedServiceIdentity: ...
def resolve_host_id_for_placement(private_doc: object, placement_token: str) -> str: ...
def allocate_fdb_port(accepted: AcceptedClusterState, host_id: str, *, default_port: int = 4550) -> int: ...
def infer_service_placement(private_doc: object, accepted: AcceptedClusterState, *, network: str, service_id: str, default_port: int = 4550) -> tuple[ServicePlacement, object]: ...

# FDB-OFM-STATE-001 accepted_state.py
def read_accepted_state(ctx: FdbContext, network: str) -> AcceptedClusterState | None: ...
def build_accepted_state(...) -> AcceptedClusterState: ...
def publish_accepted_generation(...) -> AcceptedClusterState: ...
def build_retired_state(...) -> AcceptedClusterState: ...
def publish_retired_generation(...) -> AcceptedClusterState: ...
def reject_retired_lineage_mutation(state: AcceptedClusterState | None) -> None: ...

# FDB-OFM-STATE-002 operation_store.py
def read_operation_state(ctx: FdbContext, network: str, operation_id: str | None = None) -> object: ...
def create_prepared_operation(ctx: FdbContext, prepared: PreparedOperation) -> PreparedOperation: ...
def record_progress(ctx: FdbContext, progress: OperationProgress) -> OperationProgress: ...
def require_retry_identity(state: object, operation: OperationIdentity) -> None: ...
def mark_terminal(ctx: FdbContext, operation: OperationIdentity, result: object) -> object: ...
def record_supersession(...) -> object: ...

# FDB-OFM-STATE-003 scopes.py
def assert_scope_available(ctx: FdbContext, network: str, operation: OperationIdentity) -> None: ...
def acquire_scope(ctx: FdbContext, network: str, operation: OperationIdentity) -> object: ...
def assert_no_conflicting_scope(ctx: FdbContext, network: str, operation: OperationIdentity) -> None: ...
def release_scope(ctx: FdbContext, network: str, operation: OperationIdentity) -> None: ...
def transfer_scope(...) -> object: ...

# FDB-OFM-STATE-004 contract_state.py
def select_contract_generation(ctx: FdbContext, network: str, draft: ConsumerContractDraft) -> int: ...
def publish_contract_artifact(ctx: FdbContext, network: str, artifact: ConsumerContractArtifact) -> EvidenceRef: ...

# FDB-OFM-DEPLOY-001 coolify.py
def inventory_services(binding: HostBindingRef, network: str) -> tuple[object, ...]: ...
def get_service(binding: HostBindingRef, deployment_id: str) -> object | None: ...
def create_service(binding: HostBindingRef, request_id: str, descriptor: object) -> object: ...
def update_service(binding: HostBindingRef, request_id: str, deployment_id: str, descriptor: object) -> object: ...
def deploy_service(binding: HostBindingRef, request_id: str, deployment_id: str) -> object: ...
def stop_service(binding: HostBindingRef, request_id: str, deployment_id: str) -> object: ...
def remove_service(binding: HostBindingRef, request_id: str, deployment_id: str) -> object: ...

# FDB-OFM-FDB-002 fdbcli.py
def run_fdbcli(cluster_file: Path, args: tuple[str, ...], *, timeout_s: float) -> object: ...

# FDB-OFM-FDB-003 fdb_client.py
def run_transaction_probe(...) -> object: ...
```

The Coolify adapter must use request IDs supplied by the operation layer. It must not generate a fresh semantic operation identity on retry. The `fdbcli` adapter accepts an explicit cluster-file path and argument tuple; callers do not pass shell command strings.

## 6. Ownership, concurrency, and side-effect rules

### 6.1 One mutating operation per logical FDB cluster

The initial implementation intentionally uses a coarse, YAGNI-safe mutation rule: at most one non-terminal mutating FDB Control operation may own a logical cluster at a time. `inspect` and read-only `consumer-contract` may run concurrently. This does not hard-code service or host count; it only serializes accepted-state mutation for one logical database lineage.

The scope key is conceptually:

```text
fdb-cluster:<network>/<cluster-id>
```

No service-level parallel mutation is required by the current contract. A future relaxation requires a parent contract change and concurrency proof; it is not inferred from disjoint service IDs.

### 6.2 Exclusive writers

| Namespace/effect | Exclusive direct owner |
|---|---|
| accepted FDB state | `FDB-OFM-STATE-001` |
| prepared/progress/terminal operation state | `FDB-OFM-STATE-002` |
| mutation scope ownership | `FDB-OFM-STATE-003` |
| consumer-contract artifact/generation history | `FDB-OFM-STATE-004` |
| immutable/redacted evidence | `FDB-OFM-EV-001` |
| rendered operator reports | `FDB-OFM-EV-002` |
| Coolify external mutations | `FDB-OFM-DEPLOY-001`, requested only through `DEPLOY-003` |
| `fdbcli` external calls | `FDB-OFM-FDB-002` |
| Python FDB transaction calls | `FDB-OFM-FDB-003` |
| shared private file | none; FDB Control is read-only |
| Hub services/configuration | none; outside FDB Control |
| Mother/Besu state | none; outside FDB Control |

### 6.3 Idempotency

Every `do` retry uses the same `OperationIdentity` and the same frozen target. Every external mutation request derives a stable request ID from the operation ID plus the functionality/milestone identity. Adapters reconcile ambiguous outcomes by querying the exact target before retrying. A retry never chooses a new host, service ID, port, coordinator set, cluster ID, or FDB configuration.

### 6.4 No accidental count coupling

No module may derive service count from host count or coordinator count from raw service count, or Hub count from either. `ServicePlacement.host_id` is many-to-one by design. Normal coordinator cardinality is derived from distinct `zone_id` failure domains, not from the number of services. Any uniqueness check is scoped to the exact resource that must be unique: service ID, endpoint, port on a particular host/address, or coordinator endpoint.

## 7. Functionality-to-module composition

Every one of the 89 non-gap functionality IDs from `fdb-o-f.md` has exactly one ordered public module chain below.

| Functionality | Ordered module/public seam chain |
|---|---|
| `FDB-OF-PRIV-001` | `FDB-OFM-PRIV-001.load_private_infrastructure` |
| `FDB-OF-PRIV-002` | `FDB-OFM-PRIV-001.load_private_infrastructure` → `FDB-OFM-PRIV-002.resolve_host_binding` |
| `FDB-OF-PRIV-003` | `FDB-OFM-PRIV-002.validate_target_hosts` |
| `FDB-OF-PRIV-004` | `FDB-OFM-PRIV-002.public_binding_ref` |
| `FDB-OF-PRIV-005` | `FDB-OFM-EV-001.redact_private_material` |
| `FDB-OF-OBS-001` | `FDB-OFM-STATE-001.read_accepted_state` |
| `FDB-OF-OBS-002` | `FDB-OFM-STATE-002.read_operation_state` |
| `FDB-OF-OBS-003` | `FDB-OFM-OBS-001.inventory_services` |
| `FDB-OF-OBS-004` | `FDB-OFM-OBS-001.probe_service_runtime` |
| `FDB-OF-OBS-005` | `FDB-OFM-OBS-002.query_cluster_status` |
| `FDB-OF-OBS-006` | `FDB-OFM-OBS-001.resolve_observed_service_identities` |
| `FDB-OF-OBS-007` | `FDB-OFM-OBS-002.coordinator_observation` |
| `FDB-OF-OBS-008` | `FDB-OFM-OBS-002.redundancy_observation` |
| `FDB-OF-OBS-009` | `FDB-OFM-OBS-003.failure_domain_summary` |
| `FDB-OF-OBS-010` | `FDB-OFM-OBS-004.probe_database` |
| `FDB-OF-OBS-011` | `FDB-OFM-OBS-003.classify_drift` |
| `FDB-OF-OBS-012` | `FDB-OFM-OBS-003.calculate_failure_tolerance` |
| `FDB-OF-OBS-013` | `FDB-OFM-CTL-003.allowed_next_actions` |
| `FDB-OF-OBS-014` | `FDB-OFM-EV-001.store_observation_snapshot` → `FDB-OFM-EV-002.render_inspection` |
| `FDB-OF-CTL-001` | `FDB-OFM-CTL-001.parse_intent` |
| `FDB-OF-CTL-002` | `FDB-OFM-CTL-002.freeze_target` |
| `FDB-OF-CTL-003` | `FDB-OFM-CTL-002.build_dependency_plan` |
| `FDB-OF-CTL-004` | `FDB-OFM-STATE-003.assert_scope_available` → `FDB-OFM-STATE-003.acquire_scope` |
| `FDB-OF-CTL-005` | `FDB-OFM-STATE-002.create_prepared_operation` |
| `FDB-OF-CTL-006` | `FDB-OFM-CTL-003.revalidate_prepared_operation` |
| `FDB-OF-CTL-007` | `FDB-OFM-STATE-002.record_progress` |
| `FDB-OF-CTL-008` | `FDB-OFM-STATE-002.require_retry_identity` |
| `FDB-OF-CTL-009` | `FDB-OFM-STATE-003.assert_no_conflicting_scope` |
| `FDB-OF-CTL-010` | `FDB-OFM-STATE-002.mark_terminal` → `FDB-OFM-STATE-003.release_scope` |
| `FDB-OF-CTL-011` | `FDB-OFM-CTL-003.classify_interruption` |
| `FDB-OF-CTL-012` | `FDB-OFM-CTL-003.validate_forward_supersession` → `FDB-OFM-STATE-002.record_supersession` → `FDB-OFM-STATE-003.transfer_scope` |
| `FDB-OF-NET-001` | `FDB-OFM-NET-001.validate_service_addresses` |
| `FDB-OF-NET-002` | `FDB-OFM-NET-001.reject_container_local_only_addresses` |
| `FDB-OF-NET-003` | `FDB-OFM-NET-001.probe_fdb_reachability` |
| `FDB-OF-NET-004` | `FDB-OFM-NET-001.derive_consumer_reachable_endpoints` |
| `FDB-OF-NET-005` | `FDB-OFM-NET-001.validate_transport_security` |
| `FDB-OF-SVC-001` | `FDB-OFM-DEPLOY-003.resolve_service_identity` |
| `FDB-OF-SVC-002` | `FDB-OFM-DEPLOY-003.capture_service_descriptor` |
| `FDB-OF-SVC-003` | `FDB-OFM-DEPLOY-002.render_service_descriptor` |
| `FDB-OF-SVC-004` | `FDB-OFM-DEPLOY-003.create_or_update_service` |
| `FDB-OF-SVC-005` | `FDB-OFM-DEPLOY-003.deploy_service` |
| `FDB-OF-SVC-006` | `FDB-OFM-DEPLOY-003.verify_deployment_health` |
| `FDB-OF-SVC-007` | `FDB-OFM-DEPLOY-003.stop_service` |
| `FDB-OF-SVC-008` | `FDB-OFM-DEPLOY-003.remove_service` |
| `FDB-OF-SVC-009` | `FDB-OFM-DEPLOY-003.restore_accepted_service` |
| `FDB-OF-FDB-001` | `FDB-OFM-FDB-004.prepare_cluster_identity` |
| `FDB-OF-FDB-002` | `FDB-OFM-FDB-001.render_cluster_file` |
| `FDB-OF-FDB-003` | `FDB-OFM-FDB-004.bootstrap_cluster` |
| `FDB-OF-FDB-004` | `FDB-OFM-FDB-005.attach_service` |
| `FDB-OF-FDB-005` | `FDB-OFM-FDB-005.verify_participation` |
| `FDB-OF-FDB-006` | `FDB-OFM-FDB-006.verify_convergence` |
| `FDB-OF-FDB-007` | `FDB-OFM-FDB-006.validate_redundancy_support` |
| `FDB-OF-FDB-008` | `FDB-OFM-FDB-006.verify_live_redundancy` |
| `FDB-OF-FDB-009` | `FDB-OFM-FDB-007.prepare_withdrawal` |
| `FDB-OF-FDB-010` | `FDB-OFM-FDB-007.verify_withdrawal_safe` |
| `FDB-OF-FDB-011` | `FDB-OFM-FDB-007.withdraw_service` |
| `FDB-OF-FDB-012` | `FDB-OFM-FDB-005.detect_partial_participation` |
| `FDB-OF-COORD-001` | `FDB-OFM-COORD-001.resolve_explicit_coordinators` |
| `FDB-OF-COORD-002` | `FDB-OFM-COORD-001.derive_topology_coordinators` |
| `FDB-OF-COORD-003` | `FDB-OFM-COORD-002.apply_coordinator_overlay` |
| `FDB-OF-COORD-004` | `FDB-OFM-COORD-002.apply_coordinator_overlay` → `FDB-OFM-OBS-002.query_cluster_status` |
| `FDB-OF-COORD-005` | `FDB-OFM-COORD-002.apply_coordinator_overlay` → `FDB-OFM-FDB-001.parse_cluster_file` |
| `FDB-OF-COORD-006` | `FDB-OFM-COORD-001.derive_topology_coordinators` |
| `FDB-OF-CFG-001` | `FDB-OFM-CFG-001.parse_configuration_intent` |
| `FDB-OF-CFG-002` | `FDB-OFM-CFG-001.validate_topology_support` |
| `FDB-OF-CFG-003` | `FDB-OFM-CFG-001.apply_configuration` |
| `FDB-OF-CFG-004` | `FDB-OFM-CFG-001.verify_configuration` |
| `FDB-OF-AUTH-001` | `FDB-OFM-STATE-001.build_accepted_state` |
| `FDB-OF-AUTH-002` | `FDB-OFM-STATE-001.publish_accepted_generation` |
| `FDB-OF-AUTH-003` | `FDB-OFM-STATE-001.preserve_missing_service_membership` |
| `FDB-OF-AUTH-004` | `FDB-OFM-STATE-001.require_lineage_identity` |
| `FDB-OF-AUTH-005` | `FDB-OFM-STATE-001.build_retired_state` → `FDB-OFM-STATE-001.publish_retired_generation` |
| `FDB-OF-AUTH-006` | `FDB-OFM-STATE-001.reject_retired_lineage_mutation` |
| `FDB-OF-CON-001` | `FDB-OFM-CON-001.derive_contract` |
| `FDB-OF-CON-002` | `FDB-OFM-CON-001.canonicalize_contract` |
| `FDB-OF-CON-003` | `FDB-OFM-CON-001.compare_contracts` |
| `FDB-OF-CON-004` | `FDB-OFM-STATE-004.select_contract_generation` |
| `FDB-OF-CON-005` | `FDB-OFM-STATE-004.publish_contract_artifact` |
| `FDB-OF-CON-006` | `FDB-OFM-CON-001.rectification_required` |
| `FDB-OF-CON-007` | `FDB-OFM-CON-001.compatibility_block` |
| `FDB-OF-CON-008` | `FDB-OFM-CON-001.transport_security_block` |
| `FDB-OF-CON-009` | `FDB-OFM-CON-001.namespace_block` |
| `FDB-OF-CON-010` | `FDB-OFM-CON-001.retired_contract` |
| `FDB-OF-EV-001` | `FDB-OFM-EV-001.capture_prestate` |
| `FDB-OF-EV-002` | `FDB-OFM-EV-001.capture_frozen_target` |
| `FDB-OF-EV-003` | `FDB-OFM-EV-001.record_verified_milestone` |
| `FDB-OF-EV-004` | `FDB-OFM-EV-001.capture_finalized_poststate` |
| `FDB-OF-EV-005` | `FDB-OFM-EV-001.capture_contract_delta` |
| `FDB-OF-EV-006` | `FDB-OFM-EV-002.render_result` |

### 7.1 Contract-open mapping rule

A chain may name a public seam whose mutating or externally meaningful semantics remain contract-open. The seam still exists so tests and callers have one owner, but it MUST raise the corresponding section-16 `FDB_OPEN_*` error before its first effect until the parent gap is closed. The module chain itself does not authorize an implementation to fill the gap.

## 8. Operation and stage binding

### 8.1 Read-only operations

`FDB-OFM-APP-002 inspect.run` expands the exact `FDB-OP-INSPECT` pipeline from `fdb-o-f.md`. It may call only read/derived seams. If the bounded transaction probe remains contract-open, inspection reports that proof as unavailable/blocked rather than silently substituting process health.

`FDB-OFM-APP-003 consumer_contract.run` expands the exact `FDB-OP-CONSUMER-CONTRACT` pipeline. It may derive and publish only the FDB consumer artifact. It never discovers Hub membership and never mutates a Hub.

### 8.2 Mutating operation shell

Every mutating app module exposes the same shape:

```python
def prep(ctx: FdbContext, request: object) -> OperationCommandResult: ...
def do(ctx: FdbContext, network: str, operation_id: str) -> OperationCommandResult: ...
def finalize(ctx: FdbContext, network: str, operation_id: str) -> OperationCommandResult: ...
```

The app module writes nothing directly. `prep` performs read-only observation/planning, then requests operation/scope/evidence state writes from their owners. `do` requests only frozen live effects. `finalize` freshly observes reality, verifies postconditions, derives the consumer contract, then requests accepted-state publication and terminal close in that order.

### 8.3 App-module operation binding

| Operation | App module | Lifecycle |
|---|---|---|
| `FDB-OP-INSPECT` | `FDB-OFM-APP-002` | one shot |
| `FDB-OP-CONSUMER-CONTRACT` | `FDB-OFM-APP-003` | one shot |
| `FDB-OP-CREATE-CLUSTER` | `FDB-OFM-APP-004` | prep → do → finalize |
| `FDB-OP-ADD-SERVICE` | `FDB-OFM-APP-005` | prep → do → finalize |
| `FDB-OP-REMOVE-SERVICE` | `FDB-OFM-APP-006` | prep → do → finalize |
| `FDB-OP-REPLACE-SERVICE` | `FDB-OFM-APP-007` | prep → do → finalize |
| `FDB-OP-SET-COORDINATORS` | `FDB-OFM-APP-008` | prep → do → finalize |
| `FDB-OP-CONFIGURE-CLUSTER` | `FDB-OFM-APP-009` | prep → do → finalize |
| `FDB-OP-RECONCILE` | `FDB-OFM-APP-010` | prep → do → finalize |
| `FDB-OP-RETIRE-CLUSTER` | `FDB-OFM-APP-011` | prep → do → finalize |

### 8.4 Finalization order

For every mutating operation except `reconcile`, successful finalization follows this authority order:

```text
fresh deployment/FDB observation
  -> operation-specific postcondition proof
    -> bounded usability proof when required
      -> derive post-operation consumer contract
        -> publish accepted FDB generation
          -> publish terminal evidence/contract artifact
            -> mark operation terminal
              -> release mutation scope
```

`reconcile` omits accepted-generation publication when accepted intent is unchanged. Repairing live reality is not a semantic topology generation change.

### 8.5 No rollback branch

There is no app module, parser branch, module ID, state transition, or error-recovery path named rollback. Interrupted state is handled by exact retry, fresh inspection, reconcile-to-accepted-intent, or the still-contract-open explicit forward-supersession path.

## 9. Runtime state ownership

### 9.1 Canonical root

FDB Control owns only:

```text
runtime/state/fdb/<network>/
```

The shared private input remains outside this root:

```text
runtime/state/mother/identity.private.yaml
```

and is read-only to FDB Control.

### 9.2 Reserved child namespaces

| Relative namespace | Owner | Purpose |
|---|---|---|
| `accepted/` | `FDB-OFM-STATE-001` | accepted topology/configuration/retirement generations; exact schema/publication mechanism remains gap-012 open |
| `operations/` | `FDB-OFM-STATE-002` | prepared/progress/terminal operation records; exact schema/atomicity remains gap-012 open |
| `scopes/` | `FDB-OFM-STATE-003` | logical mutation ownership; exact persistence remains gap-012 open |
| `consumer-contract/` | `FDB-OFM-STATE-004` | derived consumer-contract history/artifacts; exact schema remains gap-015 open |
| `evidence/` | `FDB-OFM-EV-001` | immutable/redacted evidence objects |
| `reports/` | `FDB-OFM-EV-002` | disposable human/JSON reports |

No other module writes directly below these namespaces. Container state directories and FDB data volumes are deployment/runtime resources, not FDB Control authority files.

### 9.3 Cluster files

The FDB cluster file used by a deployed FDB service or Hub is runtime dependency material, not accepted-state authority by itself. `FDB-OFM-FDB-001` owns deterministic content derivation; the deployment layer decides where the service-local copy is materialized. FDB Control evidence may hash the contents, but a path on one host is never treated as the universal consumer path.

## 10. Automatic placement and mutation harness contracts

### 10.1 Automatic add-service placement

`FDB-OFM-DEPLOY-004` owns the deterministic normal add-service placement
calculation. It receives an already loaded private document and accepted cluster
state; it performs no file or network writes. The exact service syntax is
`<network><placement>-fdb<N>`. The placement token resolves to exactly one
configured Coolify controller, `FDB-OFM-PRIV-002` supplies that controller's FDB-routable
address, and the allocator chooses the first unused accepted port beginning at
`4550`. The complete result is frozen by `FDB-OFM-APP-005.prep_inferred`.

`do` and `finalize` consume the frozen placement. They do not call the allocator
again.

### 10.2 Mutation harness

`FDB-OFM-HARNESS-001` is an operator-driver, not an orchestrator authority. Its
fixed current step sequence is:

```text
pre-inspect -> prep -> do -> operation-inspect -> finalize -> final-inspect
```

Only `do` and `finalize` are mutating harness steps. Both
`--execute-mutations` and `--yes-i-know-this-mutates-fdb` are required to cross
the mutation boundary. Without both, the harness stops after prep and prints the
resolved host/endpoint returned by FDB Control.

The harness state schema is `main-computer.fdb-mutate-harness.v1`. Run state is
stored beneath `runtime/state/fdb/harness-runs/`. `--resume` loads that state and
continues after `last_completed_step`; it does not recreate or reinterpret the
prepared operation.

For add/remove service mutations, final inspection must prove accepted generation
`start + 1`, unchanged cluster description, the exact frozen target coordinator
set, cluster-ID change exactly when that set changed, and the exact requested
service presence/absence. The harness prints that proof summary before declaring
completion.

The repository export surface MUST include `fdb_mutate_harness.py` as an explicit
root export item in `export-main-computer-test.ps1`; the export contract test
guards that inclusion so snapshots used for future FDB work do not silently lose
the harness.


## 11. External adapter contracts

### 11.1 Coolify

`FDB-OFM-DEPLOY-001` is the only module that speaks the Coolify HTTP API. It resolves project/environment/server/destination context from explicit inputs and non-secret host bindings, performs bounded requests, returns typed responses, and never decides FDB topology.

The existing `tools/coolify_fdb_cluster.py` contains reusable implementation evidence for private-state parsing, Coolify context resolution, service payloads, Compose rendering, create/update/deploy calls, cluster-file generation, and redaction. New code may migrate those fragments behind the declared modules. The new package MUST NOT call legacy `plan/apply` as its internal authority, because that script couples multiple services per symbolic host into one service and lacks accepted-state, operation, convergence, removal, and consumer-contract semantics.

### 11.2 `fdbcli`

`FDB-OFM-FDB-002` owns subprocess execution. It receives an explicit cluster file, argument tuple, timeout, and operation/request context. It returns exit code, stdout/stderr bytes or validated text, timing metadata, and ambiguity classification. Protocol modules parse/interpret only through declared public functions; they do not shell out independently.

### 11.3 Python FDB client

`FDB-OFM-FDB-003` is the only module that imports the external FoundationDB Python package `fdb`. Because the control package is named `fdb_control`, import shadowing is prohibited by construction. API version selection and probe behavior remain blocked where the parent compatibility/probe contracts are open.

### 11.4 Network probing

`FDB-OFM-NET-001` owns bounded socket/reachability checks. It may prove reachability to explicit endpoints; it may not infer service membership from an open port. TLS-specific verification remains disabled until `FDB-OF-GAP-006` closes.

## 12. Service, host, and process model

The implementation must preserve three independent layers:

```text
host
  placement + physical failure-domain context

service
  accepted FDB Control membership unit; many services may share one host

runtime process
  one or more observed fdbserver processes belonging to a service
```

The exact one-service-to-process cardinality remains `FDB-OF-GAP-011`. Therefore no module may hard-code exactly one `fdbserver` process per accepted service. Observation models use tuples of runtime process observations, and service-level proofs aggregate only according to the parent contract once it is closed.

Service IDs are never derived from host IDs. A default presentation may include a host label, but identity equality is explicit, not string-pattern based.

## 13. Retry and reconciliation

### 12.1 Exact retry

A retry of `do` or `finalize` reloads the same prepared operation and rejects a changed intent. It may reobserve reality to determine which frozen milestones are already satisfied. Already-satisfied milestones are not replayed merely because the local process restarted.

### 12.2 Ambiguous live effects

When Coolify, `fdbcli`, or another external call times out after dispatch, the adapter must return an ambiguous outcome rather than success/failure by guess. The owning protocol re-reads the exact target state and either records the milestone as proved, retries the same request identity, or returns `inspect-first`.

### 12.3 Reconcile

`reconcile` is the only ordinary operation that may restore drift without changing accepted intent. The app module consumes the drift classification and calls only the exact restoration seams permitted by the prepared plan. It may restore a missing accepted service, accepted addressing, accepted coordinator set, or accepted FDB configuration; it may not invent a new service or remove an accepted one.

### 12.4 Forward supersession

Until `FDB-OF-GAP-009` closes, an abandoned prepared target with overlapping cluster scope blocks a new mutating operation. No module may clear the scope or rewrite the frozen target merely because the operator wants to proceed.

## 14. Consumer-contract handoff

The consumer contract is the only ordinary FDB Control artifact intended for the separate Hub-FDB rectifier. The handoff direction is:

```text
FDB Control
  -> consumer-contract artifact + generation/hash + rectification-required flag
    -> hub-fdb-rectify
      -> Hub configuration/runtime
```

FDB Control does not call the rectifier automatically. A finalized FDB mutation always re-derives the contract and records whether it changed. A topology generation may advance while the consumer-contract generation remains unchanged.

The contract must contain only Hub-relevant FDB dependency facts. Controller credentials, private keys, raw Coolify tokens, or other deployment secrets are prohibited. TLS references may be included only after the parent TLS contract defines how references are represented without embedding private key material.

## 15. Evidence and reporting

`FDB-OFM-EV-001` stores evidence from typed source facts; it does not scrape rendered text. Every mutating operation must retain, as applicable: accepted prestate hash, observed prestate hash, frozen target, affected services/hosts/failure domains, coordinator pre/post state, FDB configuration pre/post state, verified live milestones, fresh finalized poststate, usability/convergence proof references, accepted-generation result, consumer-contract pre/post hashes, and the rectification-required decision.

Secret redaction occurs before ordinary evidence publication. Reports are derived from typed evidence/results and are disposable. Human and JSON renderers must agree on the underlying typed facts.

## 16. Open boundaries inherited from `fdb-o-f.md`

These gaps remain open. The module layer names one owner and one fail-closed code; it does not fill the missing parent contract.

| Gap | Blocked module/call family | Exact fail-closed code | Parent decision required |
|---|---|---|---|
| `FDB-OF-GAP-001` | FDB-OF-OBS-010 / FDB-OFM-OBS-004 | `FDB_OPEN_TRANSACTION_PROBE` | Exact probe keyspace, timeout, retry, cleanup, and non-destructive semantics |
| `FDB-OF-GAP-002` | FDB-OF-FDB-006 / FDB-OFM-FDB-006 | `FDB_OPEN_CONVERGENCE_PROOF` | Exact service-convergence threshold and evidence |
| `FDB-OF-GAP-003` | FDB-OF-FDB-008 / FDB-OFM-FDB-006 | `FDB_OPEN_REDUNDANCY_FINALIZATION` | Exact final redundancy/data-placement proof |
| `FDB-OF-GAP-004` | FDB-OF-FDB-009..011 / FDB-OFM-FDB-007 | `FDB_OPEN_SERVICE_WITHDRAWAL` | Exact exclusion/evacuation/withdrawal semantics |
| `FDB-OF-GAP-005` | FDB-OF-CFG-001/003/004 / FDB-OFM-CFG-001 | `FDB_OPEN_NATIVE_CONFIGURATION` | Initial supported FDB-native configuration set and exact mutation contract |
| `FDB-OF-GAP-006` | FDB-OF-NET-005, CON-008 / FDB-OFM-NET-001, CON-001 | `FDB_OPEN_TLS_TRANSPORT` | TLS material reference/distribution/rotation/verification model |
| `FDB-OF-GAP-007` | FDB-OF-CON-007 / FDB-OFM-CON-001 | `FDB_OPEN_CLIENT_COMPATIBILITY` | Exact client/server/API compatibility representation/rules |
| `FDB-OF-GAP-008` | FDB-OF-CON-009 / FDB-OFM-CON-001 | `FDB_OPEN_NAMESPACE_CONTRACT` | Exact namespace/application-scope ownership and serialization |
| `FDB-OF-GAP-009` | FDB-OF-CTL-012 / FDB-OFM-CTL-003, STATE-002/003 | `FDB_OPEN_FORWARD_SUPERSESSION` | Forward supersession record and scope-transfer semantics |
| `FDB-OF-GAP-010` | retire-cluster do path / FDB-OFM-APP-011, STATE-001 | `FDB_OPEN_RETIREMENT_DATA_POLICY` | Data-retention versus destructive-volume semantics |
| `FDB-OF-GAP-011` | service/process model / CORE-001, OBS-001, FDB-005 | `FDB_OPEN_SERVICE_PROCESS_MODEL` | Exact relationship between accepted service and runtime fdbserver process set |
| `FDB-OF-GAP-012` | accepted/operation persistence / STATE-001..003 | `FDB_OPEN_STATE_PERSISTENCE` | Exact persistence schemas and atomicity mechanism |
| `FDB-OF-GAP-013` | service descriptor / DEPLOY-002 | `FDB_OPEN_SERVICE_DESCRIPTOR` | Exact descriptor schema beyond the closed normal add-service placement/address/port allocator |
| `FDB-OF-GAP-015` | consumer contract schema / CON-001, STATE-004 | `FDB_OPEN_CONSUMER_CONTRACT_SCHEMA` | Exact canonical schema/serialization and generation advancement |

### 15.1 Surface-open items

- `FDB-OF-GAP-016` is closed here: canonical module entry is `python -m tools.fdb_control`.
- `FDB-OF-GAP-017` is closed for normal `add-service`: `python -m tools.fdb_control add-service prep <network> --service <service-id>` infers and freezes host/address/port. Composite target syntax for other operations remains surface-open.
- `FDB-OF-GAP-018` remains open: exact final JSON schema names and human presentation are owned by reporting work; no presentation choice may change typed semantics.

### 15.2 Fail-before-effect rule

A public call blocked by one of the tabled gaps MUST validate enough input to identify the requested blocked behavior, then return its exact `FDB_OPEN_*` error before: scope acquisition, operation-record creation, evidence write, report write, Coolify call, socket mutation, subprocess dispatch, FDB client call, accepted-state write, or consumer-contract publication. Read-only higher-level commands may report the blocked capability as unavailable if they can otherwise complete without invoking it.

## 17. Requirement-to-module coverage

The module layer satisfies the mandatory ownership areas listed by `fdb-o-f.md` section 20 as follows:

| Required ownership area | Module owner(s) |
|---|---|
| shared-private loading and host binding | `FDB-OFM-PRIV-001`, `FDB-OFM-PRIV-002` |
| automatic add-service placement/address/port allocation | `FDB-OFM-DEPLOY-004`, `FDB-OFM-PRIV-002` |
| operator mutation harness | `FDB-OFM-HARNESS-001` |
| accepted-state persistence | `FDB-OFM-STATE-001` |
| operation records/scopes/retry state | `FDB-OFM-STATE-002`, `FDB-OFM-STATE-003`, `FDB-OFM-CTL-003` |
| Coolify/deployment transport | `FDB-OFM-DEPLOY-001` |
| service descriptor rendering | `FDB-OFM-DEPLOY-002` |
| FDB CLI/native client observation | `FDB-OFM-FDB-002`, `FDB-OFM-FDB-003`, `FDB-OFM-OBS-002` |
| FDB transaction probe | `FDB-OFM-OBS-004 via FDB-OFM-FDB-003` |
| cluster birth/bootstrap | `FDB-OFM-FDB-004` |
| service participation/convergence | `FDB-OFM-FDB-005`, `FDB-OFM-FDB-006` |
| service evacuation/removal | `FDB-OFM-FDB-007` |
| coordinator management | `FDB-OFM-COORD-001` |
| FDB-native configuration | `FDB-OFM-CFG-001` |
| consumer-contract derivation/canonicalization | `FDB-OFM-CON-001`, `FDB-OFM-STATE-004` |
| inspection/evidence output | `FDB-OFM-EV-001`, `FDB-OFM-EV-002`, `FDB-OFM-APP-002` |

## 18. Contract-test architecture

### 17.1 Test hierarchy

The target hierarchy is:

```text
tests/fdb/
  specification/
    test_fdb_document_graph.py
    test_fdb_traceability.py
  unit/
    test_models.py
    test_privates.py
    test_host_bindings.py
    test_cluster_file.py
    test_drift.py
    test_consumer_contract.py
    ...
  contract/
    test_inspect_contract.py
    test_create_cluster_contract.py
    test_add_service_contract.py
    test_remove_service_contract.py
    test_replace_service_contract.py
    test_set_coordinators_contract.py
    test_configure_cluster_contract.py
    test_reconcile_contract.py
    test_consumer_contract_operation.py
    test_retire_cluster_contract.py
  integration/
    test_coolify_adapter_fake_server.py
    test_fdbcli_adapter_fake_process.py
```

Live remote smokes are separate from deterministic contract acceptance and must not be required for ordinary unit collection.

### 17.2 Traceability marker

Every FDB contract test carries metadata equivalent to:

```python
@pytest.mark.fdb_contract(
    operations=["FDB-OP-..."],
    functionalities=["FDB-OF-..."],
    modules=["FDB-OFM-..."],
    methods=["FDB-OFM-....public_call"],
)
```

Collection rejects unknown IDs, missing module ancestry, a method not present in the declared functionality chain, or a mutating test that attempts to cross a documented open gap without proving the exact fail-before-effect code.

### 17.3 Mandatory test classes

At minimum, tests must prove:

1. multiple accepted services can share one host without identity collision;
2. host count and service count remain independent in inspection and safety calculations;
3. multiple services in one failure domain do not inflate the derived coordinator count;
4. add/remove/replace freeze the exact topology-derived coordinator target before effects, and coordinator-service removal moves/proves authority before deletion;
5. deployment success cannot satisfy FDB participation verification;
6. FDB participation cannot publish accepted authority before finalization;
7. `reconcile` cannot change accepted membership;
8. every retry preserves operation ID and frozen target;
9. ambiguous external outcomes trigger re-observation rather than a guessed result;
10. every finalized mutation re-derives consumer-contract state;
11. FDB Control never mutates Hub services;
12. FDB Control never reads Mother journals/topology as authority;
13. shared privates are read-only and secret bytes never enter ordinary evidence;
14. a missing observed service remains accepted until an explicit topology mutation finalizes;
15. retirement cannot be inferred from an empty/unreachable runtime;
16. contract-open calls fail before the first effect with the exact `FDB_OPEN_*` code.

## 19. Migration from the experimental deployer

`tools/coolify_fdb_cluster.py` remains implementation evidence, not the new control surface. Migration follows these rules:

1. extract generic validation/private-binding/Coolify transport/Compose-rendering behavior into the declared new modules only after matching contract tests exist;
2. do not preserve the legacy assumption of one Coolify service per host as FDB service identity;
3. do not preserve `plan/apply` as an alternate operator lifecycle;
4. do not preserve the legacy behavior where every configured FDB instance automatically becomes a coordinator;
5. do not preserve Hub-topology files as FDB authority;
6. do not import the legacy script from new operation modules; once equivalent functionality is covered, the legacy entry point can become a compatibility wrapper or be retired by a separate cleanup patch;
7. tests for the legacy tool remain valid only for behavior intentionally retained; they do not override this document.

## 20. Implementation order

A dependency-safe implementation sequence is:

1. `CORE-001` through `CORE-006`;
2. read-only shared-private and host-binding modules, with gap-014 fail-closed behavior where exact mapping is required;
3. read-only path/state readers and evidence/reporting foundations;
4. Coolify read adapter, deployment inventory, network checks, `fdbcli` read adapter, live FDB status normalization, drift classification;
5. `inspect` with explicit unavailable results for contract-open probes rather than guessed success;
6. cluster-file derivation and consumer-contract semantic derivation; external contract publication remains blocked until gap-015 closes;
7. operation store/scope/accepted-state writers only after gap-012 closes;
8. service descriptor and lifecycle modules only after gap-013 closes;
9. cluster bootstrap and service participation;
10. convergence/redundancy finalization after gaps 002/003 close;
11. coordinator management;
12. service evacuation/removal after gap-004 closes;
13. FDB-native configuration mutation after gap-005 closes;
14. mutating app modules, enabling only stages whose complete dependency graph is closed;
15. retirement mutation after gap-010 closes;
16. forward supersession only after gap-009 closes;
17. TLS/client-compatibility/namespace contract additions only after gaps 006-008 close.

A later step may not ship a private implementation of an unfinished lower-level contract.

## 21. Implementation completeness checklist

- every non-gap functionality ID in `fdb-o-f.md` appears exactly once in section 7;
- every module ID maps to exactly one source file;
- every public effectful seam has one authority owner;
- operation modules write no files and call no external systems directly;
- new FDB Control code imports no `tools.mother.*` module;
- new FDB Control orchestration imports no Hub service lifecycle module;
- shared private configuration is read-only and ordinary evidence is secret-free;
- service identity is independent from host identity and multiple services per host are accepted;
- normal add-service placement is inferred only during prep and frozen; do/finalize never reallocate host/address/port;
- the mutation harness invokes only the public CLI and never becomes an alternate topology or infrastructure authority;
- coordinator membership is independent from service membership and is derived for normal membership mutations from distinct `zone_id` failure domains;
- the coordinator overlay preserves valid current coordinators first, chooses at most one per failure domain, and uses the largest supported odd cardinality (minimum one);
- no fixed topology cardinality is hard-coded;
- accepted state, observed deployment state, observed FDB state, operation state, and consumer contract remain distinct;
- every live mutation uses stable operation/request identity and independent post-effect observation;
- accepted authority changes only after fresh finalization proof;
- `reconcile` does not semantically advance accepted membership/configuration;
- every finalized mutation re-derives the consumer contract;
- Hub-FDB rectification is reported but never performed;
- no rollback command/module/state exists;
- every open contract fails before effects with its documented code;
- tests provide machine-checkable operation → functionality → module → method ancestry.

## 22. Machine-checkable traceability

The conformance suite parses the four governing documents rather than maintaining a separate requirements database. It verifies at least:

- exactly ten canonical `FDB-OP-*` operation IDs from `fdb-o.md`;
- exactly 89 non-gap `FDB-OF-*` functionality IDs from `fdb-o-f.md`;
- exactly 49 stable `FDB-OFM-*` module IDs from this document;
- every non-gap functionality expands to one declared ordered module chain;
- every referenced module ID exists;
- every operation is bound to one app module;
- no functionality chain references an undeclared public method;
- dependency direction follows section 2.5;
- persistent namespaces have one direct owner;
- external effect families have one adapter owner;
- open-gap module seams carry the exact section-16 fail-closed code;
- source SHA-256 pins at the top of this document match the reviewed parent files.

The derived traceability report is not runtime authority. Runtime code must not read the documentation parser or a generated API registry to decide behavior.

## 23. Final implementation rule

Code follows the chain:

```text
fdb.md architectural invariant
  -> fdb-o.md operator intent
    -> fdb-o-f.md functionality/stage placement
      -> fdb-o-f-m.md module/public seam
        -> traced contract test
          -> implementation
```

When implementation pressure exposes a missing answer, work stops at the highest governing document that should own that answer. The contract is corrected there first, downstream source-hash pins are updated, and only then do tests and implementation move forward.

The operator intent remains the unit of requested change. The functionality remains the unit of composable behavior. The module remains the unit of code ownership. The service remains the normal FDB membership unit. The host remains placement/failure-domain context. The consumer contract remains the FDB→Hub dependency artifact. None of those concepts may be collapsed for implementation convenience.
