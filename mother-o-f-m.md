# Mother operation-functionality-module specification

Status: module-level implementation specification companion to `mother-o-f.md`

Sources:

```text
mother.md
SHA-256: f140d2b2f27979757146d1baf53820fcfb8bcdde30e18518a295eee2b26c2364

mother-o.md
SHA-256: 39c676c61b8a09ea2a4194cc315a56a9bda1d753dc1deadeeab0136546c56211

mother-o-f.md
SHA-256: 492f81972c4d3cd8fcb38fc4927e0e2eb03625fabb36fead03e97d40d82d9d3a
```

## 1. Purpose and authority

This document specifies the implementation modules that compose every
functionality in `mother-o-f.md`.

The complete decomposition is:

```text
operation
  stage
    ordered functionality
      ordered module call
        typed public API
          state or side-effect boundary
```

`mother.md` governs architecture and safety. `mother-o.md` governs the
operator-visible operation catalog. `mother-o-f.md` governs operation,
stage, and functionality placement. This document governs module boundaries,
public APIs, dependency direction, state ownership, and the module composition
of each functionality.

If the documents conflict, the earlier document in that precedence order
governs. This document MUST NOT invent a weaker authority, rollback,
membership, bootstrap, recovery, or finalization model.

This document describes the required `tools/mother/` implementation surface.
A module listed here is a specified implementation target until traced contract
tests and retained execution evidence prove that the module exists and conforms.

## 2. Module decomposition rules

### 2.1 Module boundary

A module is a Python source unit with:

- one coherent responsibility and ownership boundary;
- a small typed public API;
- declared inputs, outputs, dependencies, and side effects;
- one authority class;
- defined idempotency, retry, and interruption behavior;
- stable error codes;
- no hidden mutation outside its declared boundary.

A module is not an individual helper, class, journal object, protocol message,
or test. Private helpers MAY exist inside the owning module. A helper that
acquires independent state ownership, performs an undeclared side effect, or
is imported by unrelated domains MUST become a declared module.

### 2.2 Reuse and placement

A shared module keeps one stable module ID wherever it is used. Section 7
places ordered module calls under every canonical functionality. The operation
and stage placement of that functionality is inherited exactly from
`mother-o-f.md`; section 8 binds each operation to its entry module and parent
pipeline.

An operation module MAY narrow parameters for a particular operation. It MUST
NOT bypass, replace, reorder, or weaken the canonical module chain for a
functionality.

### 2.3 No hidden-module rule

Production code MUST NOT:

- mutate Mother state from an undeclared module;
- write an authoritative pointer except through `MOTHER-OFM-AUTH-004`;
- write immutable authority objects except through the declared state or
  authority modules;
- call Coolify, guards, QBFT, routing, Hub/FDB, or a remote participant except
  through the declared adapter;
- read private identity material outside `MOTHER-OFM-STATE-004` and
  `MOTHER-OFM-ID-001`;
- log, serialize, or return private material through reports or evidence;
- release a lock, scope, reservation, frame, or membership fence outside its
  owner module;
- infer success from transport success;
- silently repair or adopt state while executing an observation module.

Release ownership is single-layered: `MOTHER-OFM-AUTH-*` modules release D026
reservations and authority-protocol fencing, `MOTHER-OFM-RB-*` modules close
rollback-frame ownership, `MOTHER-OFM-REC-003` closes D029/D028 reseal protocol
ownership, and `MOTHER-OFM-CTL-*` modules alone release logical mutation scopes
and current-operation ownership.

### 2.4 Module authority classes

| Class | Permitted effect |
|---|---|
| `pure` | Deterministic calculation; no I/O |
| `reader` | Stable read or external observation; no mutation |
| `derived-writer` | Writes replay-derived local generations or reports only |
| `ledger-writer` | Writes operation, scope, request, frame, or planning records |
| `live-adapter` | Performs a prepared reversible infrastructure mutation |
| `local-authority-writer` | Changes a local active-generation or recovered-head pointer |
| `replicated-authority-writer` | Constructs or commits network authority |
| `orchestrator` | Orders modules but owns no lower-level persistence or external protocol |

The assigned class is the maximum effect permitted to any public method in the
module. A `live-adapter` MAY also expose read-only observation methods without
becoming a second class. A lower-class method does not authorize a caller to
invoke the module's higher-class methods without the corresponding operation,
frame, lock, and authority inputs.

### 2.5 Status values

| Status | Meaning |
|---|---|
| `specified` | API and boundary are sufficiently defined for tests and code |
| `surface-open` | Internal module contract is defined; public CLI spelling is not |
| `contract-open` | Parent authority or rollback contract is insufficient for safe implementation |
| `conditional` | Module is required only when the prepared operation meets the stated condition |

Schema migration and identity/secret rotation retain the `contract-open`
status from `mother-o-f.md`. Their modules define seams and rejected states,
but authority-changing implementations MUST remain disabled until the parent
contracts are closed.

## 3. Package and dependency architecture

### 3.1 Target package

```text
tools/mother/
  mother.py
  diagnose.py
  plan.py
  evidence_ops.py
  add_node.py
  remove_node.py
  restore_service.py
  reseal_qbft.py
  rpc_propagate.py
  sync_state.py
  recover_head.py
  reseal_state.py
  replica_enroll.py
  replica_retire.py
  migrate_schema.py
  rotate_identity.py
  repair_projections.py
  rollback.py
  common/
    models.py
    errors.py
    canonical.py
    hashing.py
    paths.py
    schemas.py
    capabilities.py
    evidence.py
    reporting.py
    compatibility.py
    atomic_files.py
    object_store.py
    faultpoints.py
    inventory.py
    coolify.py
    guards.py
    topology.py
    replica_reports.py
    sealed_state.py
    assertions.py
    intent.py
    planning.py
    operations.py
    locks.py
    barriers.py
    operation_state.py
    journal.py
    checkpoints.py
    projections.py
    private_state.py
    generations.py
    endpoints.py
    call_runner.py
    request_journal.py
    successor_reservations.py
    certificates.py
    authorization.py
    head_commit.py
    replication.py
    finalization.py
    reconciliation.py
    bootstrap_authority.py
    prestate.py
    rollback_stack.py
    rollback_journal.py
    restoration.py
    replica_membership.py
    enrollment.py
    membership_decision.py
    network_birth.py
    identity.py
    services.py
    qbft.py
    routing.py
    hub_fdb.py
    governance.py
    state_sync.py
    recovery.py
    authority_reseal.py
    migrations.py
    rotation.py
    projection_repair.py
```

### 3.2 Allowed dependency direction

```text
operation entry modules
  -> control and protocol modules
    -> state modules and live adapters
      -> core modules
```

Core modules MUST NOT import control, protocol, adapter, or operation modules.
State modules MAY import core modules only. Live adapters MAY import core
modules and vendor clients, but MUST NOT import operation modules. Protocol
modules MAY import state, transport, adapter, and core modules. Operation
modules MAY import any declared lower layer.

Cross-domain coordination belongs in an operation module or a declared
protocol module. Circular imports are prohibited.

### 3.3 Composition semantics

An ordered module chain in section 7 means:

1. each named public API returns successfully and its output validates;
2. the next call receives immutable typed output, not a reread approximation;
3. a failure stops the chain unless the parent functionality explicitly
   requires reconciliation or rollback;
4. completed durable effects are reported in the returned result or error;
5. an orchestrator never translates `unknown` into success or failure without
   calling the declared reconciliation module.

Ubiquitous imports such as models, errors, canonical encoding, and hashing are
declared in the module registry but omitted from a section 7 chain unless their
call is itself a required functional step.

Every section 7 call uses `MOTHER-OFM-CORE-001` models and
`MOTHER-OFM-CORE-002` error envelopes at its public boundary. Every declared
durable-write, pointer, dispatch, live-mutation, and protocol transition
boundary invokes the named `MOTHER-OFM-CORE-013` faultpoint hook. These
dependencies are implicit in each row and MUST NOT be implemented as alternate
paths around the owning module.

## 4. Shared type and error contract

### 4.1 Required typed values

`MOTHER-OFM-CORE-001` owns immutable dataclasses or equivalent typed models for:

| Type | Required fields |
|---|---|
| `ContentHash` | algorithm, lowercase digest |
| `HeadTuple` | journal identity, sequence, entry hash, authorization-bundle hash, state hash, head ID, head epoch |
| `AuthorityGeneration` | predecessor head tuple or synthetic birth generation, current replicas, authority participants |
| `ReplicaSets` | current, prospective, transition, desired, retiring, successor-authority |
| `OperationIdentity` | operation ID, request ID, network, operation kind |
| `OperationIntent` | operation kind, network, explicit targets, mode, options, reason, client request identity |
| `OperationRecord` | identity, intent, frozen inputs, scopes, state, allowed commands, immutable evidence roots |
| `MutationScope` | type, canonical resource identity, authority generation, owning operation |
| `RollbackSelector` | all, count, or through-layer selection plus optional operation-ID safety cross-check |
| `OperationCommandResult` | operation identity, prior/current state, durable effects, evidence/report refs, allowed next actions |
| `SuccessorReservation` | authority generation, successor entry hash, participant set, receipts, status |
| `CertificateRef` | kind, schema version, object hash, bound entry hash |
| `AuthorizationBundleRef` | bundle hash, entry hash, certificate kind and hash, optional membership roots |
| `RollbackFrame` | frame ID, operation ID, phase, typed prestate ref, restore contract, status |
| `ParticipantRequest` | request ID, operation ID, participant, method, path, body hash |
| `ParticipantResult` | durable state, result hash, target rejection, transport observation |
| `StateGeneration` | generation ID, immutable root, manifest hash, active-pointer predecessor |
| `EvidenceRef` | object hash, schema, redaction policy, source and observation time |

Every cross-module value MUST use these models or a versioned schema-owned
model. Bare dictionaries MUST NOT cross a public module boundary.

### 4.2 Error envelope

All expected failures use `MotherError` with:

```text
code
message
operation_id
module_id
retry_class
authority_effect
durable_effect_refs
evidence_refs
allowed_next_actions
cause_class
```

`retry_class` is one of `never`, `same-request`, `after-reobserve`, or
`operator-decision`. `authority_effect` is one of `none`, `ledger-only`,
`live-state-maybe-changed`, `local-pointer-determined`, or
`network-head-determined`.

Public module APIs MUST NOT throw untyped vendor exceptions across their
boundary. Secret values MUST NOT appear in any error field.

### 4.3 Minimum error namespaces

| Namespace | Examples |
|---|---|
| `MOTHER_INPUT_*` | invalid intent, unsupported option, ambiguous target |
| `MOTHER_SCHEMA_*` | unsupported schema, malformed object, capability absent |
| `MOTHER_STATE_*` | unstable read, hash mismatch, replay failure, pointer changed |
| `MOTHER_CONFLICT_*` | scope conflict, active operation, lock generation mismatch |
| `MOTHER_TRANSPORT_*` | endpoint denied, request unknown, call-runner unavailable |
| `MOTHER_TARGET_*` | durable participant rejection or incompatible result |
| `MOTHER_AUTH_*` | reservation conflict, invalid certificate, bundle mismatch |
| `MOTHER_ROLLBACK_*` | prestate incomplete, restore unverified, LIFO violation |
| `MOTHER_MEMBERSHIP_*` | readiness mismatch, unreachable current replica, decision conflict |
| `MOTHER_RECOVERY_*` | no unanimous candidate, invalid closure, reseal base mismatch |
| `MOTHER_LIVE_*` | postcondition failure, unhealthy service, topology disagreement |
| `MOTHER_OPEN_*` | implementation blocked by a parent `contract-open` decision |

## 5. Stable module registry

The public API names below are normative implementation seams. Signatures are
Python-like specifications; implementations MAY use classes where lifecycle
or dependency injection requires them, but MUST preserve the typed inputs,
outputs, effects, and error behavior.

### 5.1 Operation entry modules

All operation entry modules are `orchestrator`, write no state directly, and
return `OperationCommandResult`.

| Module ID | Path | Public API and responsibility |
|---|---|---|
| `MOTHER-OFM-APP-001` | `mother.py` | `main(argv) -> int`; command registry, argument handoff, stable exit-code mapping, no domain logic |
| `MOTHER-OFM-APP-002` | `diagnose.py` | `diagnose(ctx, network, options) -> DiagnosticReport`; read-only observation pipeline |
| `MOTHER-OFM-APP-003` | `plan.py` | `plan(ctx, intent) -> PlanReport`; candidate calculation without locks or mutation |
| `MOTHER-OFM-APP-004` | `evidence_ops.py` | `inspect_evidence(ctx, query)` and `export_evidence(ctx, query, destination)`; read/export surface |
| `MOTHER-OFM-APP-005` | `add_node.py` | `prep`, `do`, `finalize`, `rollback`; add/reactivate/bootstrap validator flow |
| `MOTHER-OFM-APP-006` | `remove_node.py` | `prep`, `do`, `finalize`, `rollback`; validator removal and zero-validator continuity |
| `MOTHER-OFM-APP-007` | `restore_service.py` | `prep`, `do`, `finalize`, `rollback`; saved-identity service repair |
| `MOTHER-OFM-APP-008` | `reseal_qbft.py` | `prep`, `do`, `finalize`, `rollback`; in-place hard QBFT repair |
| `MOTHER-OFM-APP-009` | `rpc_propagate.py` | `run`; typed owned-route propagation with rollback frames |
| `MOTHER-OFM-APP-010` | `sync_state.py` | `prep`, `do`, `finalize`, `rollback`; local stale-state adoption |
| `MOTHER-OFM-APP-011` | `recover_head.py` | `prep`, `do`, `finalize`, `rollback`; lost local state-root recovery |
| `MOTHER-OFM-APP-012` | `reseal_state.py` | `prep`, `do`, `finalize`, `rollback`; unanimous authority-restoring reseal |
| `MOTHER-OFM-APP-013` | `replica_enroll.py` | `prep`, `do`, `finalize`, `rollback`; ordinary prospective-replica enrollment |
| `MOTHER-OFM-APP-014` | `replica_retire.py` | `prep`, `do`, `finalize`, `rollback`; reachable ordinary replica retirement |
| `MOTHER-OFM-APP-015` | `migrate_schema.py` | disabled `prep`, `do`, `finalize`, `rollback` seams; raise `MOTHER_OPEN_MIGRATION_AUTHORITY` until parent contract closes |
| `MOTHER-OFM-APP-016` | `rotate_identity.py` | disabled `prep`, `do`, `finalize`, `rollback` seams; raise `MOTHER_OPEN_ROTATION_AUTHORITY` until parent contract closes |
| `MOTHER-OFM-APP-017` | `repair_projections.py` | `run`; local-only atomic replay-derived repair |
| `MOTHER-OFM-APP-018` | `rollback.py` | `rollback(ctx, selector) -> OperationCommandResult`; exact cancellation and strict-LIFO restore lifecycle |

### 5.2 Core modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-CORE-001` | `common/models.py` | Immutable models from section 4 plus schema-versioned serializers | `pure`; rejects unknown enum/schema values |
| `MOTHER-OFM-CORE-002` | `common/errors.py` | `MotherError`, `wrap_vendor_error`, exit-code mapping | `pure`; never includes secret-bearing values |
| `MOTHER-OFM-CORE-003` | `common/canonical.py` | `canonical_json(value) -> bytes`, `canonical_yaml(value) -> bytes` | `pure`; deterministic UTF-8, normalized keys, no floats or ambiguous scalars in hashed objects |
| `MOTHER-OFM-CORE-004` | `common/hashing.py` | `sha256(bytes)`, `hash_file`, `ordered_root`, `set_root` | `pure` except file read; validates algorithm and canonical member ordering |
| `MOTHER-OFM-CORE-005` | `common/paths.py` | Resolve canonical Mother roots and validate contained paths | `pure`; rejects traversal, symlink escape, wrong network, and wrong generation |
| `MOTHER-OFM-CORE-006` | `common/schemas.py` | `load_schema`, `validate_object`, `validate_schema_transition` | `reader`; unknown version is a hard compatibility failure |
| `MOTHER-OFM-CORE-007` | `common/capabilities.py` | `read_capabilities`, `require_capabilities`, `freeze_capability_set` | `reader`; missing or changed capability blocks the caller |
| `MOTHER-OFM-CORE-008` | `common/evidence.py` | `store_evidence`, `load_evidence`, `export_manifest`, `redact_copy` | immutable local writes only; content-addressed, flushed, secret-redacted export |
| `MOTHER-OFM-CORE-009` | `common/reporting.py` | `render_json`, `render_text`, `render_allowed_commands`, and typed report builders | `derived-writer`; rendering never changes authority |
| `MOTHER-OFM-CORE-010` | `common/compatibility.py` | `check_peer_compatibility`, `freeze_contract_versions` | `reader`; returns exact missing schema/capability evidence |
| `MOTHER-OFM-CORE-011` | `common/atomic_files.py` | `stable_read`, `durable_create`, `durable_replace`, `atomic_pointer_cas` | local durable I/O; temp-write, file fsync, rename/replace, directory fsync; CAS mismatch never overwrites |
| `MOTHER-OFM-CORE-012` | `common/object_store.py` | `put_immutable`, `get_verified`, `copy_verified_closure`, `verify_closure` | immutable local I/O; existing hash with different bytes is fatal corruption |
| `MOTHER-OFM-CORE-013` | `common/faultpoints.py` | `hit(name, context)` and production no-op/test interruption implementations | `pure` in production; cannot mutate state, suppress errors, or select an alternate algorithm |

### 5.3 Observation and external adapter modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-OBS-001` | `common/inventory.py` | `build_inventory(snapshot_sources) -> InventorySnapshot`; normalize services, hosts, identities, markers, and ownership | `reader`; partial inventory is explicit and never silently accepted as complete |
| `MOTHER-OFM-OBS-002` | `common/coolify.py` | `observe_service`, `observe_host`, `apply_service_change`, `restore_service_change` | observation or prepared `live-adapter`; every mutation requires frozen desired state, prestate ref, and request ID |
| `MOTHER-OFM-OBS-003` | `common/guards.py` | `probe_guard`, `probe_runtime`, `verify_process_policy` | `reader`; unknown reachability and stale reports remain unknown |
| `MOTHER-OFM-OBS-004` | `common/topology.py` | `merge_observations`, `calculate_dependencies`, `verify_topology_closure` | `pure`; contradiction set is preserved, not auto-resolved |
| `MOTHER-OFM-OBS-005` | `common/replica_reports.py` | `collect_reports`, `validate_report`, `freeze_report_set` | `reader`; full expected-set completeness is caller-selectable and required by authority protocols |
| `MOTHER-OFM-OBS-006` | `common/sealed_state.py` | `classify(observations) -> SealedStateClassification` | `pure`; exactly one classification or explicit contradiction |
| `MOTHER-OFM-OBS-007` | `common/assertions.py` | `run_assertion_set`, `verify_assertion_evidence` | `reader`; returns every failed/unknown assertion with immutable evidence refs |

### 5.4 Planning and operation-control modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-CTL-001` | `common/intent.py` | `parse_intent`, `validate_mode`, `normalize_selector` | `pure`; rejects implicit targets, ambiguous aliases, and incompatible options |
| `MOTHER-OFM-CTL-002` | `common/planning.py` | `calculate_desired_state`, `order_functionalities`, `calculate_scopes`, `build_rollback_contract`, `candidate_sets` | `pure`; deterministic from frozen observations and explicit intent |
| `MOTHER-OFM-CTL-003` | `common/operations.py` | `create_prepared`, `load_operation`, `publish_current`, `inspect_active`, `release_operation` | `ledger-writer`; immutable record plus replay-derived current pointer |
| `MOTHER-OFM-CTL-004` | `common/locks.py` | `acquire_full_set`, `acquire_scope`, `renew`, `release_with_proof`, `inspect_locks` | `ledger-writer`; all-or-fail acquisition, authority-generation fencing, no partial success exposed |
| `MOTHER-OFM-CTL-005` | `common/barriers.py` | `evaluate_clean_state`, `evaluate_reachability`, `evaluate_mutation_barrier`, `revalidate_frozen` | `reader`; unknown or incomplete required input blocks mutation |
| `MOTHER-OFM-CTL-006` | `common/operation_state.py` | `transition`, `allowed_transitions`, `validate_terminal`, `reconcile_from_durable_effect` | `ledger-writer`; compare-and-swap state transition, idempotent same transition, illegal transition rejected |

### 5.5 State and generation modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-STATE-001` | `common/journal.py` | `read_stable_head`, `load_entry`, `load_bundle`, `walk_back`, `replay_forward`, `validate_lineage`, `build_entry_bytes` | `reader` plus immutable entry construction; never updates a head pointer |
| `MOTHER-OFM-STATE-002` | `common/checkpoints.py` | `locate_newest_valid`, `build_checkpoint`, `validate_checkpoint`, `state_closure` | immutable checkpoint construction; future-object hashes prohibited from checkpoint state |
| `MOTHER-OFM-STATE-003` | `common/projections.py` | `render_generation`, `compare_generation`, `build_manifest`, `publish_generation` | `derived-writer`; publication uses one flushed pointer CAS |
| `MOTHER-OFM-STATE-004` | `common/private_state.py` | `read_private_state`, `resolve_validator_ref`, `build_recovery_closure`, `install_verified_private_state` | secret-bearing reader/writer; strict permissions, no general serialization, no plaintext evidence |
| `MOTHER-OFM-STATE-005` | `common/generations.py` | `create_staging`, `seal_generation`, `discard_unpublished`, `switch_active`, `reconcile_active` | local generation writer; active pointer is the commit determinant |

### 5.6 Invocation and transport modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-XPORT-001` | `common/endpoints.py` | `resolve_private_endpoint`, `authorize_target` | `pure` plus inventory read; rejects public, unowned, unapproved method/path/host tuples |
| `MOTHER-OFM-XPORT-002` | `common/call_runner.py` | `dispatch`, `query_status`, `fetch_result` | transport adapter only; never claims target success without durable request evidence |
| `MOTHER-OFM-XPORT-003` | `common/request_journal.py` | `get_or_create_request`, `record_observation`, `resolve_state`, `classify_failure` | `ledger-writer`; same request/body is idempotent, request/body mismatch is fatal |

### 5.7 Authority and finalization modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-AUTH-001` | `common/successor_reservations.py` | `acquire_full_set`, `resume`, `collect_receipts`, `cancel_full_set`, `release` | replicated reservation writer; exact authority generation and entry hash, all-or-fail current set |
| `MOTHER-OFM-AUTH-002` | `common/certificates.py` | `build_successor_certificate`, `validate_successor_certificate`, `build_ack_certificate`, `validate_ack_certificate`, `validate_acceptances` | `pure` plus immutable object write; full exact set only |
| `MOTHER-OFM-AUTH-003` | `common/authorization.py` | `build_bundle`, `validate_bundle`, `derive_certificate_refs` | replicated-authority object construction; bundle binds existing entry and post-entry evidence only |
| `MOTHER-OFM-AUTH-004` | `common/head_commit.py` | `commit_entry_bundle_pair`, `read_commit_outcome` | sole network-head pointer writer; immutable entry and bundle fsync precede atomic pair commit |
| `MOTHER-OFM-AUTH-005` | `common/replication.py` | `replicate_closure`, `verify_replica`, `resync_exact_head`, `collect_acknowledgements` | distributed authority transport; exact closure and replayed state verification |
| `MOTHER-OFM-AUTH-006` | `common/finalization.py` | `prepare_intent`, `build_finalization_entry`, `verify_terminal_membership`, `complete_release` | finalization protocol; commit and replication-pending states remain distinct |
| `MOTHER-OFM-AUTH-007` | `common/reconciliation.py` | `reconcile_head_commit`, `reconcile_replication`, `reconcile_cancellation` | `reader` with ledger reconciliation; durable pointer/evidence decides, never timing |
| `MOTHER-OFM-AUTH-008` | `common/bootstrap_authority.py` | `claim_birth_authority`, `accept_birth_entry`, `roll_to_ordinary_authority` | replicated authority writer for the synthetic predecessor only |

### 5.8 Rollback modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-RB-001` | `common/prestate.py` | `capture_typed`, `validate_complete`, `load_verified` | immutable prestate writer; adapter-specific schema and closure are mandatory |
| `MOTHER-OFM-RB-002` | `common/rollback_stack.py` | `arm_frame`, `checkpoint_before_dispatch`, `promote`, `load_promoted`, `resolve_provisional`, `select_lifo`, `close` | `ledger-writer`; one provisional frame, promotion only after verified postcondition |
| `MOTHER-OFM-RB-003` | `common/rollback_journal.py` | `record_started`, `record_step`, `record_failed`, `record_verified`, `replay` | append-only rollback ledger; operation and frame identity mandatory |
| `MOTHER-OFM-RB-004` | `common/restoration.py` | `restore_frame`, `verify_restored`, `dispatch_adapter_restore` | live-adapter coordinator; strict LIFO, frame remains active until restoration verifies |

### 5.9 Membership and network-birth modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-MEM-001` | `common/replica_membership.py` | `calculate_sets`, `freeze_sets`, `validate_terminal_evidence`, `activate`, `retire` | replicated membership protocol; no quorum fallback or unreachable-current exclusion |
| `MOTHER-OFM-MEM-002` | `common/enrollment.py` | `create_generation`, `transfer_closure`, `build_readiness`, `commit_readiness_root`, `cancel_and_tombstone` | prospective-host local/replicated preparation; generation is immutable once readiness is signed |
| `MOTHER-OFM-MEM-003` | `common/membership_decision.py` | `collect_transition_acceptances`, `persist_commit_in_progress`, `validate_decision`, `cancel_decision` | replicated decision writer; exactly one decision per authority generation |
| `MOTHER-OFM-MEM-004` | `common/network_birth.py` | `claim_synthetic_generation`, `build_birth_checkpoint`, `preserve_zero_validator_continuity`, `ordinary_rollover` | bootstrap authority; birth entry is sequence-1 initial-state checkpoint |

### 5.10 Identity, service, and topology modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-ID-001` | `common/identity.py` | `resolve_identity`, `install_reserved`, `verify_derivation`, `verify_ownership`, `recovery_material_ref` | secret-aware identity adapter; never regenerates reserved material |
| `MOTHER-OFM-SVC-001` | `common/services.py` | `resolve_service`, `capture_service_prestate`, `create_or_repair`, `establish_private_candidate`, `detach_or_remove`, `verify_policy`, `restore`, `enforce_standby` | prepared Coolify live adapter over `MOTHER-OFM-OBS-002` |
| `MOTHER-OFM-NET-001` | `common/qbft.py` | `observe_sets`, `calculate_desired`, `bootstrap`, `reactivate`, `soft_vote`, `hard_transition`, `verify_convergence`, `restore` | prepared QBFT live adapter; no vote/config mutation without frame checkpoint |
| `MOTHER-OFM-NET-002` | `common/routing.py` | `observe_owned_graph`, `calculate_desired`, `apply_transition`, `verify_hosts`, `restore` | prepared typed route adapter; cannot alter unowned routes |
| `MOTHER-OFM-NET-003` | `common/hub_fdb.py` | `observe_topology`, `calculate_desired_epoch`, `apply_transition`, `verify_epoch`, `restore` | prepared topology live adapter; exact participant and epoch set |
| `MOTHER-OFM-NET-004` | `common/governance.py` | `observe_bindings`, `calculate_binding_changes`, `apply_prepared`, `verify_bindings`, `restore` | prepared governance adapter; used when an operation has declared governance scope |

### 5.11 Local adoption, recovery, and reseal modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-REC-001` | `common/state_sync.py` | `pin_candidate`, `download_to_staging`, `verify_staging`, `prepare_activation`, `switch_pointer`, `reconcile`, `discard` | local-authority writer; never changes network head ID, epoch, or lineage |
| `MOTHER-OFM-REC-002` | `common/recovery.py` | `load_descriptor`, `prove_unanimous_candidate`, `fetch_objects`, `restore_state_root`, `replay_and_verify`, `activate_replacement_identity`, `replicate_activation` | local then replicated recovery protocol; exact expected set required |
| `MOTHER-OFM-REC-003` | `common/authority_reseal.py` | `collect_base_reports`, `prove_common_base`, `calculate_head_sets`, `classify_obligations`, `freeze_readiness_contract`, `build_intent`, `build_checkpoint`, `build_proposal`, `collect_proposal_acceptances`, `build_certificate`, `compose_membership`, `collect_completed_certificate_acceptances`, `build_bundle`, `commit`, `complete_forward`, `commit_fence_rollover`, `reconcile_proposal_dispatch`, `prepare_cancel`, `commit_or_abort_cancel` | D029 authority protocol; `freeze_readiness_contract` and `compose_membership` freeze and validate structural composition during `prep` only; membership-changing mode invokes the ordinary D028 functionality chains during `do` before completed-certificate acceptance and distinguishes pre-fence readiness cancellation from fenced D029 cancellation |

### 5.12 Maintenance modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-MAINT-001` | `common/migrations.py` | `inventory_schemas`, `preserve_source`, `resolve_migration`, `apply_declared`, `validate_graph`, `build_migrated_object`, `replicate`, disabled `commit`, `abort` | `contract-open`; deterministic staging is testable, authority commit is disabled |
| `MOTHER-OFM-MAINT-002` | `common/rotation.py` | `freeze_scope`, `dependency_graph`, `declare_prestate`, `reserve_material`, `distribute`, `rebind`, `retire_old`, `verify`, disabled `commit`, `restore` | `contract-open`; no material generation or distribution until commit/rollback boundary is defined |
| `MOTHER-OFM-MAINT-003` | `common/projection_repair.py` | `pin_head`, `replay_generation`, `write_manifest`, `recheck_head`, `publish`, `discard_or_retry` | `derived-writer`; bounded retry and no authoritative-state write |

### 5.13 Authority-class assignment

| Modules | Maximum authority class |
|---|---|
| `MOTHER-OFM-APP-001` through `MOTHER-OFM-APP-018` | `orchestrator` |
| `MOTHER-OFM-CORE-001` through `MOTHER-OFM-CORE-004`, `MOTHER-OFM-CORE-013` | `pure` |
| `MOTHER-OFM-CORE-005` through `MOTHER-OFM-CORE-007`, `MOTHER-OFM-CORE-010` | `reader` |
| `MOTHER-OFM-CORE-008`, `MOTHER-OFM-CORE-012` | `ledger-writer` immutable-object primitives |
| `MOTHER-OFM-CORE-009` | `derived-writer` |
| `MOTHER-OFM-CORE-011` | `replicated-authority-writer` atomic primitive; callable only through the exclusive owner |
| `MOTHER-OFM-OBS-001`, `MOTHER-OFM-OBS-003` through `MOTHER-OFM-OBS-007` | `reader` |
| `MOTHER-OFM-OBS-002` | `live-adapter` |
| `MOTHER-OFM-CTL-001`, `MOTHER-OFM-CTL-002` | `pure` |
| `MOTHER-OFM-CTL-003`, `MOTHER-OFM-CTL-004`, `MOTHER-OFM-CTL-006` | `ledger-writer` |
| `MOTHER-OFM-CTL-005` | `reader` |
| `MOTHER-OFM-STATE-001`, `MOTHER-OFM-STATE-002` | `reader` plus pure immutable-byte builders |
| `MOTHER-OFM-STATE-003` | `derived-writer` |
| `MOTHER-OFM-STATE-004`, `MOTHER-OFM-STATE-005` | `local-authority-writer` |
| `MOTHER-OFM-XPORT-001` | `reader` |
| `MOTHER-OFM-XPORT-002` | `live-adapter` transport |
| `MOTHER-OFM-XPORT-003` | `ledger-writer` |
| `MOTHER-OFM-AUTH-001` through `MOTHER-OFM-AUTH-006`, `MOTHER-OFM-AUTH-008` | `replicated-authority-writer` |
| `MOTHER-OFM-AUTH-007` | `ledger-writer` reconciliation |
| `MOTHER-OFM-RB-001` through `MOTHER-OFM-RB-003` | `ledger-writer` |
| `MOTHER-OFM-RB-004` | `live-adapter` |
| `MOTHER-OFM-MEM-001` through `MOTHER-OFM-MEM-004` | `replicated-authority-writer` |
| `MOTHER-OFM-ID-001`, `MOTHER-OFM-SVC-001`, `MOTHER-OFM-NET-001` through `MOTHER-OFM-NET-004` | `live-adapter` |
| `MOTHER-OFM-REC-001` | `local-authority-writer` |
| `MOTHER-OFM-REC-002`, `MOTHER-OFM-REC-003` | `replicated-authority-writer` |
| `MOTHER-OFM-MAINT-001`, `MOTHER-OFM-MAINT-002` | `replicated-authority-writer`, disabled while `contract-open` |
| `MOTHER-OFM-MAINT-003` | `derived-writer` |

## 6. Module ownership and concurrency

### 6.1 Exclusive writers

| Resource | Exclusive writer |
|---|---|
| Network journal entry/bundle head pair | `MOTHER-OFM-AUTH-004` |
| Immutable journal entries | `MOTHER-OFM-STATE-001` via object store |
| Checkpoints | `MOTHER-OFM-STATE-002` via object store |
| Successor reservations | `MOTHER-OFM-AUTH-001` |
| Finalization intent and state | `MOTHER-OFM-AUTH-006` |
| Operation record/current pointer | `MOTHER-OFM-CTL-003` |
| Operation state transitions | `MOTHER-OFM-CTL-006` |
| Logical/full-set locks | `MOTHER-OFM-CTL-004` |
| Request identity and status observations | `MOTHER-OFM-XPORT-003` |
| Rollback frames | `MOTHER-OFM-RB-002` |
| Rollback journal | `MOTHER-OFM-RB-003` |
| Private state | `MOTHER-OFM-STATE-004` |
| Active local-state generation pointer | `MOTHER-OFM-STATE-005` |
| Projection generation pointer | `MOTHER-OFM-STATE-003` or `MOTHER-OFM-MAINT-003` through that API |
| Membership readiness generation | `MOTHER-OFM-MEM-002` |
| Membership commit-in-progress decision | `MOTHER-OFM-MEM-003` |
| Recovered head identity/epoch | `MOTHER-OFM-REC-002` through declared authority APIs |
| Authority-reseal objects | `MOTHER-OFM-REC-003` through state/authority APIs |

No second module MAY write one of these resources directly.

### 6.2 Lock order and all-or-fail rule

The canonical acquisition layers are:

1. operation conflict check;
2. logical mutation scopes;
3. full current-authority host lock set;
4. prospective-host enrollment/bootstrap locks when applicable;
5. exact successor reservation;
6. per-frame live mutation checkpoint.

Within a layer, the coordinator attempts the complete frozen set. A partial
acquisition MUST be released or allowed to expire without beginning the next
layer. Safety MUST NOT depend on two writers choosing the same first host.

Lock loss, lease-generation mismatch, or participant-set drift blocks new
dispatch. The caller then reconciles durable effects before choosing retry,
rollback, or forward completion.

### 6.3 Idempotency

Every mutating public API accepts `OperationIdentity` and, where remotely
dispatched, `ParticipantRequest`. Repeating the same identity and canonical
body MUST return the same durable object or resume the same work. Reusing an
identity with different canonical bytes MUST fail.

Immutable-object creation is idempotent by content hash. Pointer writes are
idempotent only when the observed current pointer is the frozen predecessor or
already equals the intended successor. Live adapters are idempotent by
request ID plus desired-state hash and MUST verify postconditions on every
resume.

## 7. Functionality-to-module composition

This section is the canonical expansion of every functionality in
`mother-o-f.md`. An arrow means the left-hand public call completes and its
typed output becomes input to the right-hand call. Comma-separated calls
inside one module are internal ordered calls within that module.

All rows also depend on `MOTHER-OFM-CORE-001` and `MOTHER-OFM-CORE-002` at
their public boundaries. Rows with durability, dispatch, live mutation, or
protocol transitions invoke `MOTHER-OFM-CORE-013` at the named fault
boundaries.

### 7.1 Observation and classification

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-OBS-001` | `MOTHER-OFM-CORE-011.stable_read` → `MOTHER-OFM-STATE-001.read_stable_head` | One internally consistent committed `HeadTuple`, or `MOTHER_STATE_UNSTABLE_HEAD` |
| `MOTHER-OF-OBS-002` | `MOTHER-OFM-STATE-001.load_bundle` → `MOTHER-OFM-AUTH-003.validate_bundle` | Bundle bytes and validated binding to the exact entry |
| `MOTHER-OF-OBS-003` | `MOTHER-OFM-STATE-002.locate_newest_valid` → `MOTHER-OFM-STATE-001.walk_back` → `MOTHER-OFM-STATE-001.replay_forward` | Replayed canonical state plus lineage and checkpoint evidence |
| `MOTHER-OF-OBS-004` | `MOTHER-OFM-STATE-001.replay_forward` → `MOTHER-OFM-STATE-003.compare_generation` | Per-projection equal/missing/stale/corrupt classification |
| `MOTHER-OF-OBS-005` | typed `ParticipantResult` inputs produced by the parent operation's transport functionalities → `MOTHER-OFM-OBS-005.collect_reports,validate_report,freeze_report_set` | Exact expected-set report map with explicit missing/invalid members |
| `MOTHER-OF-OBS-006` | `MOTHER-OFM-OBS-002.observe_service,observe_host` → `MOTHER-OFM-OBS-001.build_inventory` | Normalized ownership-aware service/identity/host/marker inventory |
| `MOTHER-OF-OBS-007` | `MOTHER-OFM-OBS-003.probe_guard,probe_runtime` → `MOTHER-OFM-OBS-004.merge_observations` | Per-process and per-guard observed state without inferred success |
| `MOTHER-OF-OBS-008` | `MOTHER-OFM-STATE-004.resolve_validator_ref` → `MOTHER-OFM-ID-001.resolve_identity` → `MOTHER-OFM-NET-001.observe_sets` | Expected/observed validator identity, membership, and block-progress evidence |
| `MOTHER-OF-OBS-009` | `MOTHER-OFM-NET-002.observe_owned_graph` → `MOTHER-OFM-OBS-004.merge_observations` | Typed owned route graph and backend eligibility |
| `MOTHER-OF-OBS-010` | `MOTHER-OFM-NET-003.observe_topology` → `MOTHER-OFM-OBS-004.merge_observations` | Participant map and reported topology epochs |
| `MOTHER-OF-OBS-011` | `MOTHER-OFM-CTL-003.inspect_active` → `MOTHER-OFM-CTL-004.inspect_locks` → `MOTHER-OFM-CTL-006.allowed_transitions` | Active operation, scopes, lock generation, stage, and legal next states |
| `MOTHER-OF-OBS-012` | `MOTHER-OFM-RB-002.resolve_provisional,select_lifo` → `MOTHER-OFM-RB-003.replay` | Provisional frame, promoted stack, and exact restorable ranges |
| `MOTHER-OF-OBS-013` | `MOTHER-OFM-AUTH-001.resume` → `MOTHER-OFM-AUTH-006.verify_terminal_membership` → `MOTHER-OFM-MEM-003.validate_decision` | Reservation, cancellation, finalization, acknowledgement, decision, and release facts |
| `MOTHER-OF-OBS-014` | `MOTHER-OFM-OBS-004.merge_observations,verify_topology_closure` → `MOTHER-OFM-OBS-006.classify` | One sealed-state classification or explicit contradiction set |
| `MOTHER-OF-OBS-015` | `MOTHER-OFM-OBS-006.classify` → `MOTHER-OFM-CORE-009.render_allowed_commands` | Only commands legal for the classification and active operation |
| `MOTHER-OF-OBS-016` | `MOTHER-OFM-CORE-006.validate_object` → `MOTHER-OFM-CORE-007.require_capabilities` → `MOTHER-OFM-CORE-010.check_peer_compatibility` | Exact compatible schema/capability set or blocking evidence |
| `MOTHER-OF-OBS-017` | `MOTHER-OFM-OBS-007.run_assertion_set,verify_assertion_evidence` | Complete passed/failed/unknown assertion result |
| `MOTHER-OF-OBS-018` | `MOTHER-OFM-CORE-008.store_evidence,redact_copy,export_manifest` → `MOTHER-OFM-CORE-009.render_json` or `render_text` | Immutable hashed evidence manifest with no private material |

### 7.2 Planning and operation control

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-CTL-001` | `MOTHER-OFM-CTL-001.parse_intent,normalize_selector` | Explicit normalized operation intent |
| `MOTHER-OF-CTL-002` | `MOTHER-OFM-CTL-001.validate_mode` | Valid mode/options tuple or stable input error |
| `MOTHER-OF-CTL-003` | `MOTHER-OFM-CTL-005.evaluate_clean_state,evaluate_reachability,evaluate_mutation_barrier` | Barrier evidence with no required unknowns |
| `MOTHER-OF-CTL-004` | `MOTHER-OFM-STATE-001.read_stable_head` → `MOTHER-OFM-MEM-001.freeze_sets` → `MOTHER-OFM-CORE-008.store_evidence` | Frozen predecessor, generation, and replica-set evidence |
| `MOTHER-OF-CTL-005` | `MOTHER-OFM-CTL-002.calculate_desired_state` → `MOTHER-OFM-CORE-003.canonical_json` → `MOTHER-OFM-CORE-004.sha256` | Complete canonical desired state and hash |
| `MOTHER-OF-CTL-006` | `MOTHER-OFM-CTL-002.order_functionalities` | Deterministic dependency DAG and total execution order |
| `MOTHER-OF-CTL-007` | `MOTHER-OFM-CTL-002.calculate_scopes` → `MOTHER-OFM-CTL-004.acquire_scope` | Exact owned scope set or no acquired scope |
| `MOTHER-OF-CTL-008` | `MOTHER-OFM-CTL-003.inspect_active` → `MOTHER-OFM-CTL-004.inspect_locks` → `MOTHER-OFM-CTL-005.evaluate_mutation_barrier` | No conflict, or exact conflicting owner/evidence |
| `MOTHER-OF-CTL-009` | `MOTHER-OFM-CTL-002.build_rollback_contract` → `MOTHER-OFM-CORE-006.validate_object` | Ordered typed prestate, frame, restore, and verification contract |
| `MOTHER-OF-CTL-010` | `MOTHER-OFM-CORE-010.freeze_contract_versions` → `MOTHER-OFM-CORE-007.freeze_capability_set` | Immutable schema/capability requirement set |
| `MOTHER-OF-CTL-011` | `MOTHER-OFM-CTL-003.create_prepared` → `MOTHER-OFM-CORE-008.store_evidence` | Immutable prepared operation record and hash |
| `MOTHER-OF-CTL-012` | `MOTHER-OFM-CTL-006.allowed_transitions` → `MOTHER-OFM-CTL-003.publish_current` → `MOTHER-OFM-CORE-009` command renderer | Current-operation projection and allowed commands |
| `MOTHER-OF-CTL-013` | `MOTHER-OFM-CTL-003.load_operation` → `MOTHER-OFM-CTL-005.evaluate_mutation_barrier,revalidate_frozen` | Exact frozen preconditions still hold |
| `MOTHER-OF-CTL-014` | `MOTHER-OFM-CTL-006.transition` → `MOTHER-OFM-CTL-003.publish_current` | Durable legal state transition and refreshed projection |
| `MOTHER-OF-CTL-015` | `MOTHER-OFM-XPORT-003.get_or_create_request,resolve_state` | Same canonical request resumes; different body is rejected |
| `MOTHER-OF-CTL-016` | `MOTHER-OFM-CTL-006.validate_terminal` → `MOTHER-OFM-CTL-004.release_with_proof` → `MOTHER-OFM-CTL-003.release_operation` | Scopes and current ownership released only after proof |
| `MOTHER-OF-CTL-017` | `MOTHER-OFM-CTL-002.candidate_sets` | Candidate authority/participant sets with no freeze, lock, or write |

### 7.3 Invocation and transport

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-XPORT-001` | `MOTHER-OFM-OBS-001.build_inventory` → `MOTHER-OFM-XPORT-001.resolve_private_endpoint` | Exact participant and approved endpoint |
| `MOTHER-OF-XPORT-002` | `MOTHER-OFM-XPORT-001.authorize_target` | Approved host/method/path/exposure tuple |
| `MOTHER-OF-XPORT-003` | `MOTHER-OFM-XPORT-003.get_or_create_request` → `MOTHER-OFM-XPORT-002.dispatch` → `MOTHER-OFM-XPORT-003.record_observation` | Durable accepted/running/terminal request identity |
| `MOTHER-OF-XPORT-004` | `MOTHER-OFM-XPORT-002.query_status,fetch_result` → `MOTHER-OFM-XPORT-003.record_observation,resolve_state` | Durable accepted/running/succeeded/failed/unknown result |
| `MOTHER-OF-XPORT-005` | `MOTHER-OFM-XPORT-003.classify_failure` | Transport failure separated from durable target rejection/result |

### 7.4 Journaling, authority, and finalization

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-AUTH-001` | `MOTHER-OFM-CTL-004.acquire_full_set` → `MOTHER-OFM-AUTH-001.acquire_full_set,collect_receipts` | Exact full-set reservation receipts for one authority generation and entry hash |
| `MOTHER-OF-AUTH-002` | `MOTHER-OFM-AUTH-002.build_successor_certificate,validate_successor_certificate` → `MOTHER-OFM-CORE-012.put_immutable` | Valid immutable full-set successor certificate |
| `MOTHER-OF-AUTH-003` | `MOTHER-OFM-CTL-004.acquire_full_set` → `MOTHER-OFM-AUTH-001.cancel_full_set` → `MOTHER-OFM-AUTH-007.reconcile_cancellation` | Uncommitted reservation canceled on the full authority set |
| `MOTHER-OF-AUTH-004` | `MOTHER-OFM-STATE-001.build_entry_bytes` → `MOTHER-OFM-CORE-004.sha256` → `MOTHER-OFM-CORE-012.put_immutable` | Exact content-addressed pending-action entry; no future-object hash |
| `MOTHER-OF-AUTH-005` | `MOTHER-OFM-AUTH-003.build_bundle,validate_bundle` → `MOTHER-OFM-CORE-012.put_immutable` | Bundle binding the exact entry, certificate, and applicable post-entry evidence |
| `MOTHER-OF-AUTH-006` | `MOTHER-OFM-AUTH-004.commit_entry_bundle_pair` → `MOTHER-OFM-AUTH-007.reconcile_head_commit` | Atomic local head-pair commit or pointer-determined reconciled outcome |
| `MOTHER-OF-AUTH-007` | `MOTHER-OFM-AUTH-005.replicate_closure,verify_replica` → `MOTHER-OFM-AUTH-007.reconcile_replication` | Every required replica holds and replays the exact committed closure |
| `MOTHER-OF-AUTH-008` | `MOTHER-OFM-AUTH-006.prepare_intent` → `MOTHER-OFM-CORE-008.store_evidence` | Flushed finalization-prepared intent and closure root |
| `MOTHER-OF-AUTH-009` | `MOTHER-OFM-AUTH-006.build_finalization_entry` → `MOTHER-OFM-STATE-001.build_entry_bytes` → `MOTHER-OFM-CORE-012.put_immutable` | Finalization successor built only from pre-certificate facts |
| `MOTHER-OF-AUTH-010` | `MOTHER-OFM-AUTH-004.commit_entry_bundle_pair` → `MOTHER-OFM-AUTH-007.reconcile_head_commit` | Atomic commit of the already-constructed finalization entry/bundle pair |
| `MOTHER-OF-AUTH-011` | `MOTHER-OFM-AUTH-005.resync_exact_head,verify_replica` | Lagging participant advanced to exact finalization head without alternate lineage |
| `MOTHER-OF-AUTH-012` | `MOTHER-OFM-AUTH-005.collect_acknowledgements` → `MOTHER-OFM-AUTH-002.validate_acceptances` | Replay-verified durable acknowledgement set |
| `MOTHER-OF-AUTH-013` | `MOTHER-OFM-AUTH-002.build_ack_certificate` → `MOTHER-OFM-CORE-012.put_immutable` | Full-set immutable acknowledgement certificate |
| `MOTHER-OF-AUTH-014` | `MOTHER-OFM-AUTH-006.verify_terminal_membership` → `MOTHER-OFM-MEM-001.validate_terminal_evidence` | Exact activation/retirement terminal evidence |
| `MOTHER-OF-AUTH-015` | `MOTHER-OFM-AUTH-006.complete_release` → `MOTHER-OFM-AUTH-001.release` | D026 reservations and authority-protocol fencing released after terminal proof; logical scopes remain owned by `MOTHER-OF-CTL-016` |
| `MOTHER-OF-AUTH-016` | `MOTHER-OFM-AUTH-007.reconcile_head_commit,reconcile_replication` → `MOTHER-OFM-CTL-006.reconcile_from_durable_effect` | Ambiguous attempt classified by durable head/evidence |
| `MOTHER-OF-AUTH-017` | `MOTHER-OFM-AUTH-008.claim_birth_authority,accept_birth_entry` → `MOTHER-OFM-MEM-004.build_birth_checkpoint` | Accepted sequence-1 birth checkpoint under synthetic predecessor authority |
| `MOTHER-OF-AUTH-018` | `MOTHER-OFM-RB-004.verify_restored` → `MOTHER-OFM-STATE-001.build_entry_bytes` → `MOTHER-OFM-CORE-012.put_immutable` | Exact rollback-progress/completed entry matching verified restored state, ready for the parent reservation/certificate/bundle/commit functions |
| `MOTHER-OF-AUTH-019` | `MOTHER-OFM-RB-002.load_promoted` → `MOTHER-OFM-OBS-007.verify_assertion_evidence` → `MOTHER-OFM-STATE-001.build_entry_bytes` → `MOTHER-OFM-CORE-012.put_immutable` | Exact pending-action progress entry for the verified promoted phase, ready for the parent authority functions |

### 7.5 Prestate and rollback

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-RB-001` | one declared owner observation from `MOTHER-OFM-SVC-001`, `MOTHER-OFM-NET-001`, `MOTHER-OFM-NET-002`, `MOTHER-OFM-NET-003`, `MOTHER-OFM-NET-004`, or `MOTHER-OFM-STATE-004` → `MOTHER-OFM-RB-001.capture_typed,validate_complete` → `MOTHER-OFM-CORE-012.put_immutable` | Complete typed immutable prestate closure |
| `MOTHER-OF-RB-002` | `MOTHER-OFM-RB-001.load_verified` → `MOTHER-OFM-RB-002.arm_frame` | One durable armed provisional frame |
| `MOTHER-OF-RB-003` | `MOTHER-OFM-RB-002.checkpoint_before_dispatch` | Flushed frame checkpoint immediately before mutation request |
| `MOTHER-OF-RB-004` | one declared owner verifier from `MOTHER-OFM-SVC-001`, `MOTHER-OFM-NET-001`, `MOTHER-OFM-NET-002`, `MOTHER-OFM-NET-003`, `MOTHER-OFM-NET-004`, or `MOTHER-OFM-ID-001` → `MOTHER-OFM-OBS-007.verify_assertion_evidence` → `MOTHER-OFM-RB-002.promote` | Frame promoted only after complete postcondition evidence |
| `MOTHER-OF-RB-005` | `MOTHER-OFM-RB-002.resolve_provisional` → `MOTHER-OFM-XPORT-003.resolve_state` or `MOTHER-OFM-AUTH-007.reconcile_head_commit,reconcile_cancellation` as selected by the frame contract → conditional `MOTHER-OFM-RB-004.restore_frame,verify_restored` | Provisional frame promoted, restored, or left active with exact unknown evidence |
| `MOTHER-OF-RB-006` | `MOTHER-OFM-RB-002.select_lifo` → `MOTHER-OFM-RB-004.restore_frame` → `MOTHER-OFM-RB-003.record_step` | Selected promoted frames restored in strict LIFO order |
| `MOTHER-OF-RB-007` | `MOTHER-OFM-RB-001.load_verified` → `MOTHER-OFM-RB-004.verify_restored` → `MOTHER-OFM-OBS-007.run_assertion_set` | Restored prestate and active invariants verified |
| `MOTHER-OF-RB-008` | `MOTHER-OFM-RB-003.record_started,record_step,record_failed,record_verified` | Append-only rollback progress/failure evidence |
| `MOTHER-OF-RB-009` | `MOTHER-OFM-AUTH-006.prepare_intent` closure evidence → `MOTHER-OFM-RB-002.close` | Promoted frames closed but retained until terminal release |
| `MOTHER-OF-RB-010` | `MOTHER-OFM-CTL-006.validate_terminal` → `MOTHER-OFM-RB-002.close` | Rollback-frame ownership closed only after verified terminal state; logical scopes remain owned by `MOTHER-OF-CTL-016` |

### 7.6 Replica membership and birth

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-MEM-001` | `MOTHER-OFM-MEM-001.calculate_sets,freeze_sets` → `MOTHER-OFM-CORE-008.store_evidence` | Current, prospective, transition, desired, retiring, and successor-authority sets |
| `MOTHER-OF-MEM-002` | `MOTHER-OFM-STATE-005.create_staging` → `MOTHER-OFM-MEM-002.create_generation` | Immutable prospective-host generation bound to authority and operation |
| `MOTHER-OF-MEM-003` | `MOTHER-OFM-CTL-004.acquire_full_set` for the exact enrollment/bootstrap participant set | Durable generation-fenced lock set or no usable acquisition |
| `MOTHER-OF-MEM-004` | `MOTHER-OFM-STATE-004.build_recovery_closure` → `MOTHER-OFM-MEM-002.transfer_closure` → `MOTHER-OFM-CORE-012.verify_closure` → `MOTHER-OFM-STATE-004.install_verified_private_state` | Exact private state and recovery closure installed with strict permissions |
| `MOTHER-OF-MEM-005` | `MOTHER-OFM-MEM-002.build_readiness` → `MOTHER-OFM-CORE-008.store_evidence` | Immutable readiness evidence bound to generation and desired membership |
| `MOTHER-OF-MEM-006` | `MOTHER-OFM-MEM-002.commit_readiness_root` | Canonical flushed readiness root |
| `MOTHER-OF-MEM-007` | `MOTHER-OFM-MEM-003.collect_transition_acceptances` → `MOTHER-OFM-AUTH-002.validate_acceptances` | Exact prospective/transition-set acceptance of the certificate |
| `MOTHER-OF-MEM-008` | `MOTHER-OFM-MEM-003.persist_commit_in_progress,validate_decision` | One durable commit-in-progress decision per old authority generation |
| `MOTHER-OF-MEM-009` | `MOTHER-OFM-AUTH-002.validate_ack_certificate` → `MOTHER-OFM-MEM-001.activate` → `MOTHER-OFM-STATE-005.switch_active` | Prospective replica becomes active only after full acknowledgement |
| `MOTHER-OF-MEM-010` | `MOTHER-OFM-AUTH-002.validate_ack_certificate` → `MOTHER-OFM-MEM-001.retire` | Reachable retiring replica is retired only after terminal evidence |
| `MOTHER-OF-MEM-011` | `MOTHER-OFM-MEM-003.cancel_decision` → `MOTHER-OFM-MEM-002.cancel_and_tombstone` | Exact uncommitted readiness/decision canceled and generation tombstoned |
| `MOTHER-OF-MEM-012` | `MOTHER-OFM-MEM-004.claim_synthetic_generation` → `MOTHER-OFM-AUTH-008.claim_birth_authority` | One synthetic-predecessor network-birth generation |
| `MOTHER-OF-MEM-013` | `MOTHER-OFM-AUTH-008.roll_to_ordinary_authority` → `MOTHER-OFM-MEM-004.ordinary_rollover` | Born participants enter ordinary D026 successor authority |
| `MOTHER-OF-MEM-014` | `MOTHER-OFM-MEM-004.preserve_zero_validator_continuity` → `MOTHER-OFM-STATE-002.validate_checkpoint` | Born-network checkpoint, private material, and authority remain valid with zero validators |

### 7.7 Identity and service lifecycle

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-ID-001` | `MOTHER-OFM-STATE-004.read_private_state` → `MOTHER-OFM-CORE-006.validate_object` | Canonical private-state object from the fixed runtime path |
| `MOTHER-OF-ID-002` | `MOTHER-OFM-STATE-004.resolve_validator_ref` → `MOTHER-OFM-ID-001.resolve_identity` | Exact validator record reached only through the node `validator_ref` |
| `MOTHER-OF-ID-003` | `MOTHER-OFM-ID-001.resolve_identity` → `MOTHER-OFM-ID-001.install_reserved` → `MOTHER-OFM-ID-001.verify_derivation` | Reserved identity installed byte-for-byte without regeneration |
| `MOTHER-OF-ID-004` | `MOTHER-OFM-ID-001.verify_derivation,verify_ownership` | Private/public derivation and node/service ownership evidence |
| `MOTHER-OF-ID-005` | `MOTHER-OFM-STATE-004.build_recovery_closure` → `MOTHER-OFM-ID-001.recovery_material_ref` → `MOTHER-OFM-CORE-012.verify_closure` | Private recovery material retained and closure-bound without disclosure |
| `MOTHER-OF-SVC-001` | `MOTHER-OFM-OBS-002.observe_service` → `MOTHER-OFM-SVC-001.resolve_service` | Exact immutable service identity and target |
| `MOTHER-OF-SVC-002` | `MOTHER-OFM-SVC-001.capture_service_prestate` → `MOTHER-OFM-RB-001.capture_typed` | Complete service, volume, environment, runtime, and marker prestate |
| `MOTHER-OF-SVC-003` | `MOTHER-OFM-SVC-001.create_or_repair` → `MOTHER-OFM-OBS-002.apply_service_change` | Prepared service created/repaired under request identity |
| `MOTHER-OF-SVC-004` | `MOTHER-OFM-SVC-001.enforce_standby` → `MOTHER-OFM-SVC-001.establish_private_candidate` → `MOTHER-OFM-OBS-003.verify_process_policy` | Healthy private candidate with public and validator eligibility gated |
| `MOTHER-OF-SVC-005` | `MOTHER-OFM-SVC-001.detach_or_remove` → `MOTHER-OFM-OBS-002.apply_service_change` | Exact prepared detach/disable/archive/remove policy applied |
| `MOTHER-OF-SVC-006` | `MOTHER-OFM-SVC-001.verify_policy` → `MOTHER-OFM-OBS-003.verify_process_policy` | Final service/runtime/marker/exposure policy verified |
| `MOTHER-OF-SVC-007` | `MOTHER-OFM-RB-001.load_verified` → `MOTHER-OFM-SVC-001.restore` → `MOTHER-OFM-OBS-002.restore_service_change` → `MOTHER-OFM-SVC-001.verify_policy` | Captured service and runtime prestate restored |
| `MOTHER-OF-SVC-008` | `MOTHER-OFM-SVC-001.enforce_standby` → `MOTHER-OFM-NET-002.verify_hosts` | No public route or validator eligibility until proof permits it |

### 7.8 QBFT, RPC, and Hub/FDB topology

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-QBFT-001` | `MOTHER-OFM-NET-001.observe_sets,calculate_desired` | Exact current/desired validator sets and delta |
| `MOTHER-OF-QBFT-002` | `MOTHER-OFM-AUTH-008.accept_birth_entry` proof → `MOTHER-OFM-NET-001.bootstrap` → `MOTHER-OFM-NET-001.verify_convergence` | Initial validator set running on born-network state |
| `MOTHER-OF-QBFT-003` | `MOTHER-OFM-ID-001.verify_derivation` → `MOTHER-OFM-NET-001.reactivate` → `MOTHER-OFM-NET-001.verify_convergence` | Preserved validator reactivated without new network material |
| `MOTHER-OF-QBFT-004` | `MOTHER-OFM-RB-002.checkpoint_before_dispatch` → `MOTHER-OFM-NET-001.soft_vote` → `MOTHER-OFM-NET-001.verify_convergence` | Prepared live vote transition reaches exact desired set |
| `MOTHER-OF-QBFT-005` | `MOTHER-OFM-RB-002.checkpoint_before_dispatch` → `MOTHER-OFM-NET-001.hard_transition` → `MOTHER-OFM-NET-001.verify_convergence` | Prepared in-place hard transition reaches exact desired set |
| `MOTHER-OF-QBFT-006` | `MOTHER-OFM-NET-001.verify_convergence` → `MOTHER-OFM-OBS-007.run_assertion_set` | All required nodes agree and required block progress is proven |
| `MOTHER-OF-QBFT-007` | `MOTHER-OFM-RB-001.load_verified` → `MOTHER-OFM-NET-001.restore` → `MOTHER-OFM-NET-001.verify_convergence` | Captured QBFT config/data/marker/process mode restored |
| `MOTHER-OF-RPC-001` | `MOTHER-OFM-NET-002.observe_owned_graph` | Exact owned current graph and eligibility; `MOTHER-OF-RB-001` separately captures it when needed as rollback prestate |
| `MOTHER-OF-RPC-002` | `MOTHER-OFM-NET-002.calculate_desired` | Exact desired owned route graph and typed delta |
| `MOTHER-OF-RPC-003` | `MOTHER-OFM-RB-002.checkpoint_before_dispatch` → `MOTHER-OFM-NET-002.apply_transition` | Prepared typed route delta applied only to owned routes |
| `MOTHER-OF-RPC-004` | `MOTHER-OFM-NET-002.verify_hosts` → `MOTHER-OFM-OBS-007.run_assertion_set` | Every affected host and public-service invariant verified |
| `MOTHER-OF-RPC-005` | `MOTHER-OFM-RB-001.load_verified` → `MOTHER-OFM-NET-002.restore` → `MOTHER-OFM-NET-002.verify_hosts` | Captured typed route graph restored |
| `MOTHER-OF-HUB-001` | `MOTHER-OFM-NET-003.observe_topology` | Exact current participant state and topology epoch; `MOTHER-OF-RB-001` separately captures it when needed as rollback prestate |
| `MOTHER-OF-HUB-002` | `MOTHER-OFM-NET-003.calculate_desired_epoch` | Exact desired topology, participant delta, and next epoch |
| `MOTHER-OF-HUB-003` | `MOTHER-OFM-RB-002.checkpoint_before_dispatch` → `MOTHER-OFM-NET-003.apply_transition` | Prepared Hub/FDB transition applied at exact epoch |
| `MOTHER-OF-HUB-004` | `MOTHER-OFM-NET-003.verify_epoch` → `MOTHER-OFM-OBS-007.run_assertion_set` | Every affected node reports and proves desired epoch |
| `MOTHER-OF-HUB-005` | `MOTHER-OFM-RB-001.load_verified` → `MOTHER-OFM-NET-003.restore` → `MOTHER-OFM-NET-003.verify_epoch` | Captured Hub/FDB topology restored |

### 7.9 Local adoption

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-SYNC-001` | `MOTHER-OFM-CTL-004.acquire_scope` with local-adoption exclusivity → `MOTHER-OFM-CTL-003.create_prepared` | Owned local-adoption scope retained through terminal state |
| `MOTHER-OF-SYNC-002` | `MOTHER-OFM-STATE-005.reconcile_active` → `MOTHER-OFM-OBS-005.freeze_report_set` → `MOTHER-OFM-REC-001.pin_candidate` | Old local pointer/head and unanimous remote candidate pinned |
| `MOTHER-OF-SYNC-003` | `MOTHER-OFM-STATE-005.create_staging` → `MOTHER-OFM-REC-001.download_to_staging` → `MOTHER-OFM-CORE-012.verify_closure` | Complete immutable candidate closure in unpublished staging |
| `MOTHER-OF-SYNC-004` | `MOTHER-OFM-REC-001.verify_staging` → `MOTHER-OFM-STATE-001.replay_forward` → `MOTHER-OFM-STATE-003.compare_generation` → `MOTHER-OFM-STATE-004.read_private_state` | Candidate lineage, objects, private state, pending actions, and projections verify |
| `MOTHER-OF-SYNC-005` | `MOTHER-OFM-REC-001.prepare_activation` → `MOTHER-OFM-CORE-011.durable_create` | Activation-prepared evidence outside the swappable generation |
| `MOTHER-OF-SYNC-006` | `MOTHER-OFM-REC-001.switch_pointer` → `MOTHER-OFM-STATE-005.switch_active` | One flushed CAS from pinned old generation to verified candidate |
| `MOTHER-OF-SYNC-007` | `MOTHER-OFM-STATE-005.reconcile_active` → `MOTHER-OFM-REC-001.reconcile` → `MOTHER-OFM-CTL-006.reconcile_from_durable_effect` | Pointer-determined committed or pre-commit state after interruption |
| `MOTHER-OF-SYNC-008` | `MOTHER-OFM-REC-001.discard` → `MOTHER-OFM-STATE-005.discard_unpublished` | Staging discarded while old active pointer remains unchanged |

### 7.10 Lost-local-state recovery

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-REC-001` | `MOTHER-OFM-REC-002.load_descriptor` → `MOTHER-OFM-CORE-006.validate_object` → `MOTHER-OFM-MEM-001.calculate_sets` | Valid descriptor and exact expected replica set |
| `MOTHER-OF-REC-002` | `MOTHER-OFM-OBS-005.collect_reports,freeze_report_set` → `MOTHER-OFM-REC-002.prove_unanimous_candidate` | Unanimous lineage, state, pending-action, private-material, and closure proof |
| `MOTHER-OF-REC-003` | `MOTHER-OFM-REC-002.fetch_objects` → `MOTHER-OFM-CORE-012.copy_verified_closure,verify_closure` | Every required recovery object downloaded and hash-verified |
| `MOTHER-OF-REC-004` | `MOTHER-OFM-STATE-005.create_staging` → `MOTHER-OFM-REC-002.restore_state_root` → `MOTHER-OFM-STATE-004.install_verified_private_state` | Complete recovered local Mother root in immutable generation |
| `MOTHER-OF-REC-005` | `MOTHER-OFM-REC-002.replay_and_verify` → `MOTHER-OFM-STATE-001.replay_forward` → `MOTHER-OFM-STATE-003.render_generation` | Recovered journals replayed and projections rebuilt |
| `MOTHER-OF-REC-006` | `MOTHER-OFM-OBS-003.probe_guard,probe_runtime` → `MOTHER-OFM-OBS-007.run_assertion_set` → `MOTHER-OFM-REC-002.prove_unanimous_candidate` comparison | Recovered state matches guards, live assertions, and the frozen recovery candidate |
| `MOTHER-OF-REC-007` | `MOTHER-OFM-REC-002.activate_replacement_identity` → `MOTHER-OFM-STATE-001.build_entry_bytes` → `MOTHER-OFM-AUTH-004.commit_entry_bundle_pair` | Replacement head ID/epoch activation committed at its defined boundary |
| `MOTHER-OF-REC-008` | `MOTHER-OFM-REC-002.replicate_activation` → `MOTHER-OFM-AUTH-005.replicate_closure,collect_acknowledgements` → `MOTHER-OFM-AUTH-002.build_ack_certificate` | Every expected replica acknowledges replacement-head activation |

### 7.11 Authority-restoring reseal

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-RSL-001` | `MOTHER-OFM-OBS-005.collect_reports,freeze_report_set` → `MOTHER-OFM-REC-003.collect_base_reports` → `MOTHER-OFM-CORE-008.store_evidence` | Every current base-authority report plus immutable invalid-head evidence |
| `MOTHER-OF-RSL-002` | `MOTHER-OFM-STATE-001.validate_lineage,replay_forward` → `MOTHER-OFM-REC-003.prove_common_base` | Common old authority generation and replay-valid selected predecessor |
| `MOTHER-OF-RSL-003` | `MOTHER-OFM-REC-003.calculate_head_sets` → `MOTHER-OFM-CORE-004.set_root` | Observed valid head set and superseded set equal to observed valid heads minus selected predecessor |
| `MOTHER-OF-RSL-004` | `MOTHER-OFM-CTL-003.inspect_active` → `MOTHER-OFM-RB-003.replay` → `MOTHER-OFM-AUTH-001.resume` → `MOTHER-OFM-AUTH-006.verify_terminal_membership` → `MOTHER-OFM-MEM-003.validate_decision` → `MOTHER-OFM-REC-003.classify_obligations` | Each unresolved obligation preserved, carried as remediation-required, or blocks |
| `MOTHER-OF-RSL-005` | `MOTHER-OFM-REC-003.build_intent` → `MOTHER-OFM-CORE-003.canonical_json` → `MOTHER-OFM-CORE-012.put_immutable` | Prepared intent is constructed during `do`, after any actual prospective-readiness root exists, and contains only pre-entry facts plus separate obligation/closure roots |
| `MOTHER-OF-RSL-006` | `MOTHER-OFM-REC-003.build_checkpoint` → `MOTHER-OFM-STATE-002.build_checkpoint,validate_checkpoint` → `MOTHER-OFM-CORE-012.put_immutable` | Exact successor checkpoint binding `prepared_intent_hash`, not future proposal/certificate hashes |
| `MOTHER-OF-RSL-007` | `MOTHER-OFM-REC-003.build_proposal,collect_proposal_acceptances` → `MOTHER-OFM-AUTH-002.validate_acceptances` | Proposal binds intent plus entry and is accepted exactly once by every base-authority replica, installing the D029 fence |
| `MOTHER-OF-RSL-008` | `MOTHER-OFM-REC-003.build_certificate` → `MOTHER-OFM-CORE-012.put_immutable` | Authority-reseal certificate constructed from the full proposal-acceptance set; no completed-certificate acceptance occurs in this functionality |
| `MOTHER-OF-RSL-009` | `MOTHER-OFM-MEM-001.calculate_sets,freeze_sets` → `MOTHER-OFM-REC-003.freeze_readiness_contract,compose_membership` → `MOTHER-OFM-CORE-008.store_evidence` | Freeze the prospective set, expected generation, required closure and schemas, readiness-receipt contract/version, expected membership sets, and structural legality during `prep`; this functionality performs no participant mutation and creates no readiness or transition result |
| `MOTHER-OF-RSL-010` | `MOTHER-OFM-REC-003.build_bundle` → `MOTHER-OFM-AUTH-003.build_bundle,validate_bundle` → `MOTHER-OFM-CORE-012.put_immutable` | `authority-reseal` bundle with D028 roots when membership changes |
| `MOTHER-OF-RSL-011` | `MOTHER-OFM-REC-003.commit` → `MOTHER-OFM-AUTH-004.commit_entry_bundle_pair` → `MOTHER-OFM-AUTH-007.reconcile_head_commit` | Atomic authority-reseal entry/bundle head commit |
| `MOTHER-OF-RSL-012` | `MOTHER-OFM-REC-003.complete_forward` → `MOTHER-OFM-AUTH-005.replicate_closure,collect_acknowledgements` → `MOTHER-OFM-AUTH-002.build_ack_certificate` → `MOTHER-OFM-MEM-001.validate_terminal_evidence` and conditional `activate`/`retire` → `MOTHER-OFM-REC-003.commit_fence_rollover` | Reseal replicated, acknowledged, membership-completed, and D029/D028 protocol ownership closed; logical scope release remains owned by `MOTHER-OF-CTL-016` |
| `MOTHER-OF-RSL-013` | `MOTHER-OFM-REC-003.reconcile_proposal_dispatch` → `MOTHER-OFM-REC-003.prepare_cancel` | Full-set D029 cancellation prepare only; the D029 fence remains active and this functionality does not execute D028 cancellation or tombstone the D029 fence |
| `MOTHER-OF-RSL-014` | `MOTHER-OFM-REC-003.collect_completed_certificate_acceptances` → `MOTHER-OFM-CORE-004.set_root` → `MOTHER-OFM-CORE-012.put_immutable` | Full base-authority completed-certificate acceptance; pure D029 runs immediately after RSL-008, while membership-changing D029 runs only after `MOTHER-OF-MEM-007` and `MOTHER-OF-MEM-008` have made the exact D028 transition evidence durable |
| `MOTHER-OF-RSL-015` | `MOTHER-OFM-REC-003.commit_or_abort_cancel` | D029 cancellation commits or aborts only after any composed D028 full-set cancellation is terminal; logical scope release remains owned by `MOTHER-OF-CTL-016` |

For membership-changing D029+D028, the functionality expansion is stage-bound
and mechanically contiguous:

```text
prep:
  RSL-009
  → acquire logical scopes
  → record the frozen operation plan

do:
  MOTHER-OF-MEM-002 through MOTHER-OF-MEM-006
  → RSL-005
  → RSL-006
  → RSL-007
  → RSL-008
  → MOTHER-OF-MEM-007
  → MOTHER-OF-MEM-008
  → RSL-014
```

`MOTHER-OF-RSL-009` is invoked exactly once and has no `do` portion. No
`MOTHER-OFM-MEM-002` mutating readiness API or
`MOTHER-OFM-REC-003.build_intent` call is valid during `prep`.

Cancellation expands by durable evidence:

```text
readiness exists; no D029 proposal acceptance anywhere:
  reconcile proposal dispatch across every base-authority replica
  → MOTHER-OFM-MEM-002.cancel_and_tombstone
  → preserve readiness evidence
  → no D029 cancellation certificate

D029 proposal acceptance exists anywhere:
  RSL-013
  → retain D029 fence
  → MOTHER-OFM-MEM-003.cancel_decision when a D028 decision exists
  → MOTHER-OFM-MEM-002.cancel_and_tombstone
  → prove D028 cancellation terminal
  → RSL-015

completed-certificate acceptance exists anywhere:
  cancellation prohibited
  → complete the exact operation forward
```

Absence of a local acceptance is not evidence that the pre-fence branch applies.
The dispatch reconciliation report MUST cover every base-authority replica.


### 7.12 Schema migration

These chains remain `contract-open`. Staging and validation APIs are
implementable; authority-changing APIs MUST return
`MOTHER_OPEN_MIGRATION_AUTHORITY`.

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-MIG-001` | `MOTHER-OFM-MAINT-001.inventory_schemas` → `MOTHER-OFM-CORE-006.load_schema` → `MOTHER-OFM-CORE-007.require_capabilities` | Source/destination schema and capability inventory |
| `MOTHER-OF-MIG-002` | `MOTHER-OFM-MAINT-001.preserve_source` → `MOTHER-OFM-CORE-012.put_immutable` → `MOTHER-OFM-CORE-008.store_evidence` | Original bytes, hashes, and audit evidence |
| `MOTHER-OF-MIG-003` | `MOTHER-OFM-MAINT-001.resolve_migration,apply_declared` | Deterministic destination bytes from exact source bytes |
| `MOTHER-OF-MIG-004` | `MOTHER-OFM-MAINT-001.validate_graph` → `MOTHER-OFM-CORE-006.validate_object` → `MOTHER-OFM-CORE-012.verify_closure` | Complete migrated graph validates |
| `MOTHER-OF-MIG-005` | `MOTHER-OFM-MAINT-001.build_migrated_object` → `MOTHER-OFM-STATE-002.build_checkpoint` or schema-owned state builder | Content-addressed migrated checkpoint/state object |
| `MOTHER-OF-MIG-006` | `MOTHER-OFM-MAINT-001.replicate` → `MOTHER-OFM-AUTH-005.replicate_closure,verify_replica` | Full expected set verifies migrated result |
| `MOTHER-OF-MIG-007` | `MOTHER-OFM-MAINT-001` disabled `commit` | Block until predecessor, certificate, bundle, head, finalization, and rollback authority are defined |
| `MOTHER-OF-MIG-008` | `MOTHER-OFM-MAINT-001.abort` → `MOTHER-OFM-STATE-005.discard_unpublished`; pre-commit restore only | Staging canceled without changing authority |

### 7.13 Identity or secret rotation

These chains remain `contract-open`. Observation and dependency calculation
are implementable. Material generation/distribution and authority-changing
calls MUST return `MOTHER_OPEN_ROTATION_AUTHORITY` until the parent contract
defines the commit and rollback boundaries.

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-ROT-001` | `MOTHER-OFM-MAINT-002.freeze_scope` → `MOTHER-OFM-CTL-004.acquire_scope` | Exact affected identity and exposure scope |
| `MOTHER-OF-ROT-002` | `MOTHER-OFM-MAINT-002.dependency_graph` → `MOTHER-OFM-OBS-004.calculate_dependencies` → topology/governance adapters | Complete service, contract, route, and governance dependency graph |
| `MOTHER-OF-ROT-003` | `MOTHER-OFM-OBS-001.build_inventory` plus `MOTHER-OFM-NET-002.observe_owned_graph`, `MOTHER-OFM-NET-003.observe_topology`, and `MOTHER-OFM-NET-004.observe_bindings` → `MOTHER-OFM-MAINT-002.declare_prestate` → `MOTHER-OFM-CTL-002.build_rollback_contract` | Complete private/public prestate capture contract |
| `MOTHER-OF-ROT-004` | `MOTHER-OFM-MAINT-002` disabled `reserve_material` | Replacement identity reserved only after parent generation/authority decision |
| `MOTHER-OF-ROT-005` | `MOTHER-OFM-MAINT-002` disabled `distribute` → `MOTHER-OFM-STATE-004.install_verified_private_state` | Replacement material distributed through secret-safe channel |
| `MOTHER-OF-ROT-006` | `MOTHER-OFM-MAINT-002` disabled `rebind` → `MOTHER-OFM-SVC-001`, `MOTHER-OFM-NET-002`, and `MOTHER-OFM-NET-004` prepared adapters | Every frozen dependency rebound to replacement identity |
| `MOTHER-OF-ROT-007` | `MOTHER-OFM-MAINT-002` disabled `retire_old` | Superseded material retired only after commit and dependency proof |
| `MOTHER-OF-ROT-008` | `MOTHER-OFM-MAINT-002.verify` → `MOTHER-OFM-ID-001.verify_derivation,verify_ownership` → `MOTHER-OFM-OBS-007.run_assertion_set` | Replacement identity and complete dependency closure verify |
| `MOTHER-OF-ROT-009` | `MOTHER-OFM-MAINT-002` disabled `commit` | Block until rotation authority and irreversible boundary are defined |
| `MOTHER-OF-ROT-010` | `MOTHER-OFM-MAINT-002.restore` → `MOTHER-OFM-SVC-001`, `MOTHER-OFM-NET-002`, `MOTHER-OFM-NET-004`, and `MOTHER-OFM-STATE-004` declared restore APIs | Captured identity and bindings restored before commit |
| `MOTHER-OF-ROT-011` | `MOTHER-OFM-STATE-004.read_private_state`, `MOTHER-OFM-OBS-002.observe_service`, `MOTHER-OFM-NET-002.observe_owned_graph`, and `MOTHER-OFM-NET-004.observe_bindings` → `MOTHER-OFM-RB-001.capture_typed,validate_complete` → `MOTHER-OFM-CORE-012.put_immutable` | Complete just-in-time prestate immediately before first mutation |

### 7.14 Projection repair

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-PRJ-001` | `MOTHER-OFM-STATE-001.read_stable_head` → `MOTHER-OFM-MAINT-003.pin_head` | Complete authoritative local head tuple pinned |
| `MOTHER-OF-PRJ-002` | `MOTHER-OFM-MAINT-003.replay_generation` → `MOTHER-OFM-STATE-001.replay_forward` → `MOTHER-OFM-STATE-003.render_generation` | New unpublished projection generation from exact lineage |
| `MOTHER-OF-PRJ-003` | `MOTHER-OFM-STATE-003.build_manifest` → `MOTHER-OFM-MAINT-003.write_manifest` → `MOTHER-OFM-CORE-011.durable_create` | Hashed, flushed, reread-verified projection manifest |
| `MOTHER-OF-PRJ-004` | `MOTHER-OFM-STATE-001.read_stable_head` → `MOTHER-OFM-MAINT-003.recheck_head` | Current head equals every pinned tuple field |
| `MOTHER-OF-PRJ-005` | `MOTHER-OFM-MAINT-003.publish` → `MOTHER-OFM-STATE-003.publish_generation` → `MOTHER-OFM-CORE-011.atomic_pointer_cas` | One complete projection-generation pointer published |
| `MOTHER-OF-PRJ-006` | `MOTHER-OFM-MAINT-003.discard_or_retry` → `MOTHER-OFM-STATE-005.discard_unpublished` | Stale output removed and bounded retry enforced |

## 8. Operation and stage binding

The parent pipeline remains canonical. This table prevents module work from
losing the operation boundary while avoiding a second, drift-prone copy of
every functionality row. To expand an operation:

1. read its ordered functionality rows in the cited `mother-o-f.md` sections;
2. replace each functionality ID with its exact section 7 module chain;
3. execute that chain from the named entry module;
4. preserve all operation-specific conditions and verification text in the
   parent row.

| Operation or lifecycle | Operation ID | Entry module | Stages and canonical parent placement | Status |
|---|---|---|---|---|
| Diagnose | `MOTHER-OP-DIAGNOSE` | `MOTHER-OFM-APP-002` | one-shot, `mother-o-f.md` §4.1 | `specified` |
| Plan | `MOTHER-OP-PLAN` | `MOTHER-OFM-APP-003` | one-shot candidate plan, §5.1 | `surface-open` |
| Evidence inspection/export | `MOTHER-OP-EVIDENCE-EXPORT` | `MOTHER-OFM-APP-004` | one-shot read/export, §6.1 | `surface-open` |
| Add node | `MOTHER-OP-ADD-NODE` | `MOTHER-OFM-APP-005` | `prep` §7.1; `do` §7.2; `finalize` §7.3; `rollback` §7.4 | `specified` |
| Remove node | `MOTHER-OP-REMOVE-NODE` | `MOTHER-OFM-APP-006` | `prep` §8.1; `do` §8.2; `finalize` §8.3; `rollback` §8.4 | `specified` |
| Restore service | `MOTHER-OP-RESTORE-SERVICE` | `MOTHER-OFM-APP-007` | `prep` §9.1; `do` §9.2; terminal stages §9.3 | `surface-open` |
| Reseal QBFT | `MOTHER-OP-RESEAL-QBFT` | `MOTHER-OFM-APP-008` | `prep` §10.1; `do` §10.2; terminal stages §10.3 | `specified` |
| RPC propagate | `MOTHER-OP-RPC-PROPAGATE` | `MOTHER-OFM-APP-009` | one-shot staged pipeline, §11.1 | `surface-open` |
| Sync state | `MOTHER-OP-SYNC-STATE` | `MOTHER-OFM-APP-010` | `prep` §12.1; `do` §12.2; `finalize` §12.3; `rollback` §12.4 | `specified` |
| Recover head | `MOTHER-OP-RECOVER-HEAD` | `MOTHER-OFM-APP-011` | `prep` §13.1; `do` §13.2; `finalize` §13.3; rollback boundary §13.4 | `contract-open` only for pre-activation abort/rollback |
| Reseal state | `MOTHER-OP-RESEAL-STATE` | `MOTHER-OFM-APP-012` | `prep` §14.1; `do` §14.2; `finalize` §14.3; `rollback` §14.4 | `specified` |
| Ordinary replica enrollment | `MOTHER-OP-REPLICA-ENROLL` | `MOTHER-OFM-APP-013` | `prep` §15.1; `do` §15.2; terminal stages §15.3 | `surface-open` |
| Ordinary replica retirement | `MOTHER-OP-REPLICA-RETIRE` | `MOTHER-OFM-APP-014` | staged pipeline §16.1 | `surface-open` |
| Schema migration | `MOTHER-OP-SCHEMA-MIGRATION` | `MOTHER-OFM-APP-015` | required pipeline §17.1; missing contract §17.2 | `contract-open` |
| Identity/secret rotation | `MOTHER-OP-IDENTITY-ROTATION` | `MOTHER-OFM-APP-016` | required pipeline §18.1; missing contract §18.2 | `contract-open` |
| Repair projections | `MOTHER-OP-REPAIR-PROJECTIONS` | `MOTHER-OFM-APP-017` | atomic one-shot §19.1 | `surface-open` |
| Rollback | active operation identity | `MOTHER-OFM-APP-018` | lifecycle pipeline §20.1 and range semantics §20.2 | `specified` except a parent operation's own open rollback boundary |
| Retry/resume | active operation identity | same entry module as owning operation | `do` retry §21.1; `finalize` retry §21.2 | inherits owning operation |

### 8.1 Required operation-module shell

Every staged entry module implements:

```python
def prep(ctx: MotherContext, intent: OperationIntent) -> OperationCommandResult: ...
def do(ctx: MotherContext, operation_id: OperationId) -> OperationCommandResult: ...
def finalize(ctx: MotherContext, operation_id: OperationId) -> OperationCommandResult: ...
def rollback(
    ctx: MotherContext,
    operation_id: OperationId,
    selector: RollbackSelector,
) -> OperationCommandResult: ...
```

An operation with no separate public stage MAY keep the same internal shell and
invoke it from one command. `prep` accepts intent; later stages accept the
frozen operation identity and optional safety cross-checks only. Later stages
MUST NOT accept a second desired-state description.

Each method:

1. loads or creates the operation through `MOTHER-OFM-CTL-003`;
2. checks the legal stage through `MOTHER-OFM-CTL-006`;
3. executes the parent functionality sequence expanded through section 7;
4. records every durable result before proceeding;
5. returns the exact allowed next actions.

Operation modules MUST NOT contain filesystem journal algorithms, hash
serialization, remote transport, vendor API calls, or restore implementations.

### 8.2 Command registration

`MOTHER-OFM-APP-001` owns a declarative registry:

```python
CommandSpec(
    name: str,
    operation_kind: str,
    handler: Callable[..., OperationCommandResult],
    read_only: bool,
    stages: tuple[str, ...],
    status: Literal["specified", "surface-open", "contract-open"],
)
```

A `contract-open` mutating command MAY appear in help output, but invocation
MUST stop before scope acquisition, material creation, staging, or mutation
with the corresponding `MOTHER_OPEN_*` error.

## 9. Runtime state ownership

### 9.1 Canonical root

All persistent Mother state is resolved beneath:

```text
/runtime/state/mother/
```

`MOTHER-OFM-CORE-005` is the only module that turns a logical state locator
into a filesystem path. Callers pass typed identifiers, not path fragments.

### 9.2 Path-to-owner map

| State area | Canonical owner | Other permitted access |
|---|---|---|
| `identity.private.yaml`, metadata, `private-recovery/` | `MOTHER-OFM-STATE-004` | `MOTHER-OFM-ID-001` through its API only |
| `version.json` | deployment/version installer outside operation execution | `MOTHER-OFM-CORE-007`, `010` read only |
| root `topology.yaml` and committed-state projections | `MOTHER-OFM-STATE-003` | Rebuilt only from authoritative replay |
| `active-generations/<network>.json`, `generations/<network>/` | `MOTHER-OFM-STATE-005` | Recovery/adoption modules through its API |
| `adoptions/<network>/<operation-id>/` | `MOTHER-OFM-REC-001` | Operation/reporting read |
| `locks/` | `MOTHER-OFM-CTL-004` | Observation read |
| `guards/` | guard process; `MOTHER-OFM-OBS-003` is reader | No Mother writer |
| `routes/<network>/<host>.json` | `MOTHER-OFM-NET-002` | Observation and prestate read |
| `networks/<network>/journal/` | `MOTHER-OFM-STATE-001` and `MOTHER-OFM-AUTH-004` for head commit | Checkpoint, replay, replication through APIs |
| `networks/<network>/successor-reservations/` | `MOTHER-OFM-AUTH-001` | Reconciliation/read-only inspection |
| `networks/<network>/finalization-acknowledgements/` | `MOTHER-OFM-AUTH-005` | Certificate construction/read-only inspection |
| `networks/<network>/enrollments/` | `MOTHER-OFM-MEM-002` | Membership decision/read-only inspection |
| `network-birth/<network-birth-id>/` | `MOTHER-OFM-MEM-004` | Bootstrap authority through API |
| `actions/<operation-id>/action-journal/` | `MOTHER-OFM-CTL-003` using journal engine | Operation/reporting read |
| `actions/<operation-id>/rollback-journal/` | `MOTHER-OFM-RB-003` using journal engine | Rollback replay/read |
| `actions/<operation-id>/provisional/`, `rollback-stack.json` | `MOTHER-OFM-RB-002` | Observation read |
| `actions/<operation-id>/prestate/` | `MOTHER-OFM-RB-001` | Restoration verified read |
| `actions/<operation-id>/successor-certificates/` | `MOTHER-OFM-AUTH-002` | Authorization and reporting read |
| `actions/<operation-id>/finalization/` acknowledgement certificate | `MOTHER-OFM-AUTH-002` | Finalization and reporting read |
| `actions/<operation-id>/membership-transition-decisions/` | `MOTHER-OFM-MEM-003` | Authorization and reporting read |
| request/result journals | `MOTHER-OFM-XPORT-003` | Transport and reconciliation read |
| content-addressed evidence objects/manifests | `MOTHER-OFM-CORE-008` through `MOTHER-OFM-CORE-012` | Reporting/redacted export read |
| authority-reseal intent/proposal/certificate evidence | `MOTHER-OFM-REC-003` through declared state/authority writers | Recovery/reporting read |
| `current/` | `MOTHER-OFM-CTL-003` | Diagnose/rollback read; replay-derived only |
| `reports/<operation-id>/` | `MOTHER-OFM-CORE-009` | No authority reader treats reports as source of truth |
| projection generations and active projection pointer | `MOTHER-OFM-STATE-003` | `MOTHER-OFM-MAINT-003` through its API |

### 9.3 Mother context

Dependency construction occurs once in `mother.py`. Operation modules receive:

```python
@dataclass(frozen=True)
class MotherContext:
    paths: MotherPaths
    schemas: SchemaRegistry
    capabilities: CapabilityRegistry
    object_store: ObjectStore
    journal_engine: JournalEngine
    clock: Clock
    coolify: CoolifyAdapter
    guards: GuardAdapter
    call_runner: CallRunner
    faultpoints: FaultpointController
```

`MotherContext` MUST NOT contain raw private keys, passwords, or unredacted
private-state documents. Secret-bearing values are loaded for the shortest
possible call scope through `MOTHER-OFM-STATE-004`.

Production constructors use real adapters. Tests later provide in-memory or
filesystem-backed fakes at these declared seams. Domain code MUST NOT read
environment variables or instantiate vendor clients directly.

## 10. Required write protocols

### 10.1 Immutable object write

`put_immutable` performs:

1. canonical byte construction by the schema owner;
2. content-hash calculation;
3. contained target-path resolution;
4. exclusive create of a temporary sibling;
5. complete write and file flush;
6. byte reread and hash verification;
7. atomic publish to the hash-derived path;
8. parent-directory flush;
9. verified existing-object comparison on idempotent collision.

The object is available only after step 8. Different bytes at an existing
hash-derived path are fatal corruption.

### 10.2 Stable read

`stable_read` performs pointer read A, object/bundle reads and verification,
then pointer read B. A and B MUST be byte-identical. A bounded retry MAY repeat
the complete sequence. Exhaustion returns an unstable-read error; it does not
return the last partial view.

### 10.3 Head-pair commit

`commit_entry_bundle_pair` accepts only:

```text
frozen predecessor HeadTuple
verified immutable successor entry ref
verified immutable authorization bundle ref
operation identity
expected authority generation
```

It verifies entry/bundle binding, flushes both immutable objects and their
directories, and atomically replaces `head.json` with the pair. It MUST NOT
accept object bytes built inside the commit call. A predecessor mismatch causes
no overwrite. On interruption or ambiguous return, the caller invokes
`read_commit_outcome`; the durable pointer determines the result.

### 10.4 Derived-generation publication

Projection and local-adoption generations are fully rendered and verified
before pointer publication. Publication accepts the frozen prior pointer and
one sealed generation manifest. The pointer CAS and directory flush are the
only commit. Failure before the pointer preserves the old generation. Failure
after the pointer completes forward by reconciliation.

### 10.5 Live mutation

Every live adapter mutation receives:

```text
OperationIdentity
ParticipantRequest or local request identity
frozen desired-state hash
verified typed prestate ref
armed provisional frame ID
frame checkpoint ref
adapter-specific mutation command
adapter-specific postcondition contract
```

The adapter rejects missing or mismatched values. It records no frame
promotion. The operation module verifies the postcondition and calls
`MOTHER-OFM-RB-002.promote`.

### 10.6 Secret-bearing write

Private-state writes use a private temporary file, restrictive permissions
before content write, file flush, atomic replacement, directory flush, and
post-write permission verification. Secret-bearing bytes never enter a
general-purpose object, report, error, command line, or evidence export.

## 11. Protocol object-construction order

### 11.1 Ordinary successor

```text
successor entry bytes and hash
  -> full-set reservation receipts
    -> successor certificate
      -> prospective transition evidence, when required
        -> authorization bundle
          -> atomic entry/bundle head commit
            -> exact closure replication
```

The entry MUST NOT contain its certificate hash, authorization-bundle hash, or
post-entry membership roots.

### 11.2 Authority-restoring reseal

Pure D029 construction order:

```text
prepared authority-reseal intent
  -> successor checkpoint entry binding prepared_intent_hash
    -> authority-reset proposal binding intent plus successor entry
      -> full base-authority proposal acceptances installing the D029 fence
        -> authority-reseal certificate
          -> full base-authority completed-certificate acceptances
            -> authority-reseal-certificate-acceptance-set root
              -> authorization bundle
                -> atomic entry/bundle head commit
```

Membership-changing D029+D028 construction order:

```text
prospective staging, private/recovery closure transfer, and readiness root
  -> prepared authority-reseal intent
    -> successor checkpoint entry binding prepared_intent_hash
      -> authority-reset proposal binding intent plus successor entry
        -> full base-authority proposal acceptances installing the D029 fence
          -> authority-reseal certificate
            -> D028 transition acceptances for that exact certificate
              -> D028 transition-acceptance-set root
                -> local D028 commit-in-progress transition decision
                  -> full base-authority completed-certificate acceptances binding those D028 roots
                    -> authority-reseal-certificate-acceptance-set root
                      -> authorization bundle with D029 and D028 roots
                        -> atomic entry/bundle head commit
```

The checkpoint state MUST NOT contain certificate, certificate-acceptance, bundle,
or D028 post-entry hashes. The superseded-head set contains only observed valid
network entry/bundle head tuples other than the selected predecessor. Unresolved
obligations, obligation dispositions, and recovery closures use separate roots
and cannot be extinguished by reseal.

Membership-changing D029+D028 cancellation MUST keep the active D029 fence
installed after D029 cancellation prepare, convert the local D028 decision to
cancellation-authorized with that exact D029 cancellation-prepare certificate,
finish the complete D028 full-set cancellation protocol, and only then commit or
tombstone D029 cancellation. Pure D029 MAY release the D029 fence after terminal
D029 cancellation; membership-changing D029+D028 MAY release it only after both
D029 and D028 cancellation are terminal.

### 11.3 Finalization

```text
finalization-prepared intent and frame-closure evidence
  -> finalization successor entry
    -> successor authority and authorization bundle
      -> atomic local head-pair commit
        -> finalized-replication-pending
          -> exact full-set replication and replay verification
            -> full-set acknowledgement certificate
              -> finalized and terminal release
```

Rollback remains available before the atomic finalization head-pair commit and
is closed permanently after it. Replication failure after that commit never
reopens rollback.

## 12. Retry, reconciliation, and cancellation

| Observed boundary | Required action |
|---|---|
| No durable request record | Create the exact request and dispatch |
| Request accepted/running | Query or resume the same request |
| Transport failed, target state unknown | Query durable target status; do not redispatch under a new identity |
| Live mutation maybe applied, frame provisional | Reconcile request, verify postcondition, then promote or restore |
| Immutable object exists, pointer unchanged | Reuse object or discard staging; authority unchanged |
| Intended head pair is active | Complete forward; rollback to predecessor is prohibited where commit is irreversible |
| Different head pair is active | Stop with conflict; do not overwrite |
| Finalization head active, replicas lag | Remain `finalized-replication-pending` and resync exact head |
| Membership decision accepted | Use certified completion/cancellation; one-phase unlock is prohibited |
| Local-generation pointer changed | Reconcile from the active pointer |
| Projection head changed before publish | Discard and bounded-retry from the new pinned head |

Cancellation modules accept the same authority-generation lock and exact
prepared object identity as acceptance. Before any D029 proposal acceptance,
prospective readiness MAY be canceled directly through the complete D028
readiness-cancellation path only after full base-authority dispatch
reconciliation proves that no proposal acceptance exists. After any D029
proposal acceptance, D029 cancellation prepare and D028 cancellation are
distinct terminal protocols: the D029 fence remains installed until D028
cancellation is fully proven and `MOTHER-OF-RSL-015` records the D029
cancellation commit or abort. Any completed-certificate acceptance prohibits
cancellation and requires exact forward completion. `MOTHER-OF-CTL-016`
performs logical scope and current-operation release only after durable
cancellation or terminal forward proof on every required participant.

## 13. Requirement-to-module coverage

This table extends the parent traceability chain. It points to primary modules;
the complete function-level expansion remains section 7.

| Requirement | Primary module coverage |
|---|---|
| `MOTHER-REQ-001` | Document-wide module rules, typed APIs, errors, and status gates |
| `MOTHER-REQ-002` | `MOTHER-OFM-OBS-001` through `007`, `MOTHER-OFM-STATE-001` through `003`, `MOTHER-OFM-CORE-008`, `009` |
| `MOTHER-REQ-003` | `MOTHER-OFM-STATE-004`, `MOTHER-OFM-ID-001`, `MOTHER-OFM-REC-001` through `003` |
| `MOTHER-REQ-004` | `MOTHER-OFM-STATE-001`, `002`, `003`, `MOTHER-OFM-AUTH-003` |
| `MOTHER-REQ-005` | `MOTHER-OFM-STATE-001`, `002`, `MOTHER-OFM-AUTH-001` through `006`, `MOTHER-OFM-REC-003` |
| `MOTHER-REQ-006` | `MOTHER-OFM-CTL-004`, `MOTHER-OFM-AUTH-001` |
| `MOTHER-REQ-007` | `MOTHER-OFM-RB-002`, `003`, `MOTHER-OFM-AUTH-003`, `004`, `006` |
| `MOTHER-REQ-008` | `MOTHER-OFM-CTL-005`, `MOTHER-OFM-MEM-001` through `004` |
| `MOTHER-REQ-009` | `MOTHER-OFM-RB-001` through `004`, `MOTHER-OFM-AUTH-001` through `005` |
| `MOTHER-REQ-010` | `MOTHER-OFM-NET-002`, `MOTHER-OFM-RB-001` through `004` |
| `MOTHER-REQ-011` | `MOTHER-OFM-NET-003`, `MOTHER-OFM-RB-001` through `004` |
| `MOTHER-REQ-012` | `MOTHER-OFM-APP-005` plus its section 7 expansion |
| `MOTHER-REQ-013` | `MOTHER-OFM-APP-006` plus its section 7 expansion |
| `MOTHER-REQ-014` | `MOTHER-OFM-NET-001`, `MOTHER-OFM-RB-001` through `004` |
| `MOTHER-REQ-015` | `MOTHER-OFM-CORE-006`, `007`, `010` |
| `MOTHER-REQ-016` | `MOTHER-OFM-STATE-004`, `MOTHER-OFM-CORE-012`, `MOTHER-OFM-REC-001`, `002` |
| `MOTHER-REQ-017` | `MOTHER-OFM-APP-011`, `MOTHER-OFM-REC-002` |
| `MOTHER-REQ-018` | `MOTHER-OFM-APP-010`, `MOTHER-OFM-REC-001`, `MOTHER-OFM-STATE-005` |
| `MOTHER-REQ-019` | `MOTHER-OFM-APP-017`, `MOTHER-OFM-MAINT-003`, `MOTHER-OFM-STATE-003` |
| `MOTHER-REQ-020` | `MOTHER-OFM-XPORT-001` through `003` |
| `MOTHER-REQ-021` | `MOTHER-OFM-CTL-003`, `004`, `006` |
| `MOTHER-REQ-022` | `MOTHER-OFM-AUTH-004`, `006`, `007`, `MOTHER-OFM-RB-002`, `003` |
| `MOTHER-REQ-023` | `MOTHER-OFM-AUTH-001` through `007` |
| `MOTHER-REQ-024` | `MOTHER-OFM-MEM-001` through `004`, `MOTHER-OFM-AUTH-008` |
| `MOTHER-REQ-025` | `MOTHER-OFM-AUTH-005`, `006`, `007`, `MOTHER-OFM-XPORT-001` through `003` |
| `MOTHER-REQ-026` | `MOTHER-OFM-REC-003`, `MOTHER-OFM-AUTH-001` through `004`, `MOTHER-OFM-AUTH-007`, `MOTHER-OFM-MEM-002`, `003` when membership changes |

## 14. Module acceptance contract

This document precedes tests, but each module is code-ready only when its test
contract can be derived without guessing.

### 14.1 Required test classes

| Test class | Required proof |
|---|---|
| Pure contract | Canonical input/output vectors, schema rejection, deterministic ordering and hashing |
| State contract | Exact files/objects touched, fsync/atomicity behavior, replay after interruption |
| Adapter contract | Exact request, idempotency key, target policy, observed postcondition, vendor-error translation |
| Concurrency contract | Competing operations, partial full-set acquisition, stale generation, CAS mismatch |
| Retry contract | Same request resumes; different body is rejected; unknown remains unknown until reconciled |
| Rollback contract | Complete typed prestate, armed-before-mutation, provisional resolution, strict LIFO, verified restoration |
| Authority contract | Entry/bundle acyclicity, exact participant set, commit-point crash matrix, replica verification |
| Security contract | Private-state permissions, no secret in log/error/report/evidence, endpoint exposure rejection |
| Operation contract | Parent functionality order expands to exact module calls with no skipped conditionals |

### 14.2 Mandatory faultpoints

`MotherContext.faultpoints` provides named no-op hooks in production. Tests
later MUST exercise at least:

```text
immutable.after_temp_write
immutable.after_file_fsync
immutable.after_publish_before_dir_fsync
pointer.before_cas
pointer.after_cas_before_dir_fsync
journal.after_entry_publish
journal.after_bundle_publish
journal.before_head_pair_commit
journal.after_head_pair_commit
request.after_local_record_before_dispatch
request.after_remote_accept_before_local_observation
rollback.after_frame_arm
rollback.after_dispatch_before_postcondition
rollback.after_restore_before_verification
rollback.after_verification_before_close
membership.after_readiness
membership.after_transition_acceptance
membership.after_commit_in_progress
finalization.after_local_commit
finalization.during_replication
adoption.after_activation_prepared
adoption.after_pointer_switch
projection.after_head_recheck
projection.after_pointer_publish
reseal.after_intent
reseal.after_successor_entry
reseal.after_proposal_acceptance
reseal.after_certificate
reseal.after_head_commit
```

A faultpoint MUST raise a typed simulated interruption and MUST NOT alter the
production algorithm or provide a second write path.

### 14.3 Mother test hierarchy and naming

Mother tests mirror the dependency and protocol structure rather than the
production source tree:

```text
tests/mother/
  specification/
  contracts/<dependency-layer>/
  protocols/
  operations/
  fault_injection/
  integration/
  support/
```

Specification tests verify this document graph without requiring production
modules. Contract tests use `test_<module_name>_contract.py`; protocol and fault
tests use `test_<protocol>_protocol.py` and
`test_<protocol>_faults.py`; operation tests use
`test_<operation>_pipeline.py`.

Every Mother contract test carries explicit `mother_contract` metadata naming
its requirement, operation, functionality, and module ancestry. Functionality
tests record the ordered module IDs observed. Operation tests additionally
record stage, request identity, and retained durable evidence references.

## 15. Open boundaries inherited from the parent

### 15.1 Surface-open

The following internal module contracts are specified, but the public command
shape remains open:

- standalone `plan`;
- evidence inspection/export;
- `restore-service` options;
- `rpc-propagate` options;
- standalone replica enrollment and retirement commands;
- `repair-projections`.

CLI work MAY select spelling and presentation. It MUST NOT change the module
chain or safety contract.

### 15.2 Contract-open

| Gap | Blocked modules/calls | Required parent decision |
|---|---|---|
| Pre-activation `recover-head` abort/rollback | `MOTHER-OFM-APP-011.rollback` and any recovery cleanup that could alter the pre-recovery root | Exact reversible range, evidence ownership, pointer boundary, and interruption reconciliation |
| Schema migration authority | `MOTHER-OFM-MAINT-001.commit`, `MOTHER-OFM-APP-015` mutating stages | Predecessor/successor authority, certificate and bundle kind, checkpoint semantics, finalization, cancellation, rollback |
| Identity/secret rotation authority | `MOTHER-OFM-MAINT-002.reserve_material`, `distribute`, `rebind`, `retire_old`, `commit`; `MOTHER-OFM-APP-016` mutating stages | Generation, secret distribution, commit point, dependent-binding authority, revocation, rollback, recovery |

No module implementation MAY fill these gaps by choosing a convenient local
pointer, using live facts as authority, or reusing an unrelated successor kind.

## 16. Test-first contract governance

The governing authority chain is:

```text
mother.md
→ mother-o.md
→ mother-o-f.md
→ mother-o-f-m.md
→ traced contract tests
→ implementation
```

Tests are executable verification of this document. They are not a separate
requirements source. When a test needs a signature, state transition, path
owner, error, retry rule, or side-effect answer that this hierarchy does not
provide, specification and implementation work MUST stop. The highest affected
`mother*.md` file MUST be corrected first, its downstream source-hash pins MUST
be updated, and only then MAY the test and implementation continue.

Each Mother contract test MUST carry explicit metadata equivalent to:

```python
@pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-..."],
    operations=["MOTHER-OP-..."],
    functionalities=["MOTHER-OF-..."],
    modules=["MOTHER-OFM-..."],
)
```

The Mother collection hook MUST reject:

1. unknown requirement, operation, functionality, or module identifiers;
2. contract tests with no module identifier;
3. references that present a documented `MOTHER-OF-GAP-*` item as resolved;
4. a mutating test for a `contract-open` operation or module unless the test
   proves the exact `MOTHER_OPEN_*` failure occurs before lock acquisition,
   staging, or any durable or external effect;
5. traceability metadata that conflicts with the canonical identifiers and open
   boundaries in the four governing documents.

Specification-conformance tests MAY use a separate `mother_specification`
marker because they verify the document graph itself rather than a single
module seam.

An API registry MUST NOT be required to collect, execute, or pass tests. An API
registry MUST NOT be required to implement or invoke Mother code. Runtime Mother
code MUST NOT read an API registry. CI MUST NOT treat registry absence as a
failure.

A future API registry MAY exist only as a disposable report derived from the
governing documents, traced tests, and implemented public contracts. A derived
registry MUST NOT override documentation, tests, or code contracts and MUST NOT
become an input to runtime behavior, test collection, interface selection, path
ownership, error selection, or lock selection.

Contract-open behavior is verified positively: the corresponding tests pass by
proving that the exact documented `MOTHER_OPEN_*` error is returned before the
first lock, staging action, durable write, or external effect. Specified
behavior MUST NOT be hidden behind `xfail`.

## 17. Implementation order

The dependency-safe implementation order is:

1. core models, errors, canonicalization, hashing, paths, atomic files, and
   object store;
2. schemas, capabilities, compatibility, evidence, and reporting;
3. journal, checkpoints, generations, private-state, and projections;
4. operation ledger, operation state, planning, locks, barriers, and request
   journal;
5. read-only observation and external adapters;
6. prestate, rollback stack, rollback journal, and restoration;
7. successor reservations, certificates, authorization, head commit,
   replication, finalization, and reconciliation;
8. membership, enrollment, membership decision, and network birth;
9. identity, service, QBFT, routing, Hub/FDB, and governance adapters;
10. read-only operation entry modules;
11. `sync-state`, `recover-head`, and `reseal-state` protocol modules;
12. mutating operation entry modules;
13. projection repair;
14. migration and rotation staging seams, while their mutating paths remain
    disabled.

An implementation slice is accepted only when all earlier dependencies it uses
already meet section 14. A later operation MUST NOT ship with a private copy of
an unfinished lower-level algorithm.

## 18. Implementation completeness checklist

Before production code is considered conformant:

- every stable module ID maps to one source file and documented public API;
- every public call uses typed models and typed errors;
- every functionality ID expands to an ordered module chain;
- every operation entry module expands the exact parent stage pipeline;
- every persistent path has one writer;
- every external side effect uses one declared adapter;
- every mutation has request identity and idempotency semantics;
- every reversible mutation has complete prestate and an armed frame;
- every commit boundary has interruption reconciliation;
- every authority object is constructible without a hash cycle;
- every full-set protocol rejects partial-set success;
- every pointer write uses frozen-predecessor CAS and durable directory flush;
- every private-state path preserves permissions and redaction;
- every report and projection is treated as derived;
- every `contract-open` path is disabled before its first side effect;
- module, functionality, operation, and requirement traceability is machine
  checkable.

## 19. Machine-checkable traceability

Machine-checkable traceability is supplied by explicit test metadata and
documentation-conformance tests. The tests MUST parse the canonical tables and
contracts in the four governing documents; they MUST NOT maintain a parallel
requirements or interface database.

The conformance suite MUST verify at least:

- 26 unique requirement IDs and 29 unique design IDs in `mother.md`;
- 16 unique operation IDs in `mother-o.md`;
- 169 unique non-gap functionality IDs in `mother-o-f.md`;
- 80 unique module IDs in this document;
- every operation has a functionality sequence;
- every non-gap functionality expands to exactly one declared module chain;
- every referenced module exists;
- module dependency direction follows section 3.2;
- every persistent path has one documented direct owner;
- every external effect has one adapter owner;
- `contract-open` modules remain disabled before locks and effects;
- operation-stage ordering agrees across all four documents;
- every parent source-hash pin matches the exact reviewed bytes;
- Markdown fences and normative-language rules remain valid.

Each traced contract test records:

```text
requirements
operations
functionalities
modules
```

Protocol and operation tests additionally record stage, request identity,
durable evidence references, and the ordered functionality/module calls they
observed. Collection fails immediately on unknown identifiers, missing module
ancestry, prohibited gap references, or an invalid contract-open mutation test.
These are collection errors, not delayed runtime assertions.

## 20. Final implementation rule

Code follows the chain:

```text
mother.md requirement
  -> mother-o.md operation
    -> mother-o-f.md functionality placement
      -> mother-o-f-m.md module contract
        -> traced contract test
          -> implementation
```

Tests and implementations are not alternative requirements sources. When test
or implementation pressure exposes an ambiguity, work stops at the highest
affected document. That contract is corrected and its downstream source-hash
pins and traced tests are updated before implementation continues.
