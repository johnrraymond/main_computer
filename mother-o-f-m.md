# Mother operation-functionality-module specification

Status: module-level implementation specification companion to `mother-o-f.md`

Sources:

```text
mother.md
SHA-256: b239639116941085daabf33093481d18c21127199ed2e440eabcea240dea7ef0

mother-o.md
SHA-256: 1a557a39ecbea95f65bc3dbb90988a5529b0d182cfe2ff5eea9264549d4776e4

mother-o-f.md
SHA-256: 2ab68a1b3990e65143e7b85eb943d0937435862c1e5b2d77cae05b88df0553ea
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
  upgrade_hub.py
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
    hub_release.py
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
State modules MAY import core modules only, except that
`MOTHER-OFM-STATE-002` MAY import the immutable types and read/pure-builder
seams of `MOTHER-OFM-STATE-001`. That dependency is one-way:
`MOTHER-OFM-STATE-001` MUST NOT import `MOTHER-OFM-STATE-002`. Live adapters
MAY import core modules and vendor clients, but MUST NOT import operation
modules. Protocol modules MAY import state, transport, adapter, and core
modules. Operation modules MAY import any declared lower layer.

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

#### 3.3.1 Explicit CORE-013 faultpoint-bearing functionalities

CORE-013 ancestry is granted only to the functionality IDs in this table. Each
listed functionality contains at least one method-qualified durability,
dispatch, live-mutation, pointer, or protocol-transition boundary in its
section 7 chain. Merely referencing a module whose overall authority class is
`writer`, `live-adapter`, or `transport` does not grant CORE-013 ancestry.

| Family | Faultpoint-bearing functionalities |
|---|---|
| `AUTH` | `MOTHER-OF-AUTH-001` through `013`, `MOTHER-OF-AUTH-015` through `020` |
| `CTL` | `MOTHER-OF-CTL-004`, `MOTHER-OF-CTL-007`, `MOTHER-OF-CTL-011`, `MOTHER-OF-CTL-012`, `MOTHER-OF-CTL-014` through `016` |
| `HUB` | `MOTHER-OF-HUB-003`, `MOTHER-OF-HUB-005` |
| `ID` | `MOTHER-OF-ID-003` |
| `MEM` | `MOTHER-OF-MEM-001` through `013` |
| `MIG` | `MOTHER-OF-MIG-002`, `MOTHER-OF-MIG-005`, `MOTHER-OF-MIG-006`, `MOTHER-OF-MIG-008` |
| `OBS` | `MOTHER-OF-OBS-018` |
| `PRJ` | `MOTHER-OF-PRJ-003`, `MOTHER-OF-PRJ-005`, `MOTHER-OF-PRJ-006` |
| `QBFT` | `MOTHER-OF-QBFT-002` through `005`, `MOTHER-OF-QBFT-007` |
| `RB` | `MOTHER-OF-RB-001` through `006`, `MOTHER-OF-RB-008` through `010` |
| `REC` | `MOTHER-OF-REC-003`, `MOTHER-OF-REC-004`, `MOTHER-OF-REC-007`, `MOTHER-OF-REC-008` |
| `REL` | `MOTHER-OF-REL-002` through `004`, `MOTHER-OF-REL-007` through `009`, `MOTHER-OF-REL-011`, `MOTHER-OF-REL-012` |
| `ROT` | `MOTHER-OF-ROT-001`, `MOTHER-OF-ROT-005`, `MOTHER-OF-ROT-010`, `MOTHER-OF-ROT-011` |
| `RPC` | `MOTHER-OF-RPC-003`, `MOTHER-OF-RPC-005` |
| `RSL` | `MOTHER-OF-RSL-001`, `MOTHER-OF-RSL-005`, `MOTHER-OF-RSL-006`, `MOTHER-OF-RSL-008` through `015` |
| `SVC` | `MOTHER-OF-SVC-003` through `005`, `MOTHER-OF-SVC-007`, `MOTHER-OF-SVC-008` |
| `SYNC` | `MOTHER-OF-SYNC-001` through `003`, `MOTHER-OF-SYNC-005` through `008` |
| `XPORT` | `MOTHER-OF-XPORT-003`, `MOTHER-OF-XPORT-004` |

A read-only call on a writer-capable module is not a faultpoint boundary.
Specifically, `MOTHER-OF-OBS-001` and `MOTHER-OF-OBS-013` do not receive
implicit CORE-013 ancestry. Adding or removing a faultpoint-bearing
functionality requires changing this table and the corresponding
method-qualified section 7 chain together.

## 4. Shared type and error contract

### 4.1 Required typed values

`MOTHER-OFM-CORE-001` owns immutable dataclasses or equivalent typed models for:

| Type | Required fields |
|---|---|
| `ContentHash` | algorithm, lowercase digest |
| `NetworkHeadPaths` | canonical journal-head path and committed-state projection path; named immutable value, never positional |
| `HeadTuple` | journal identity, sequence, entry hash, authorization-bundle hash, state hash, head ID, head epoch |
| `AuthorityGeneration` | predecessor head tuple or synthetic birth generation, current replicas, authority participants |
| `ReplicaSets` | current, prospective, transition, desired, retiring, successor-authority |
| `OperationIdentity` | operation ID, request ID, network, operation kind |
| `DurableEffectRef` | effect kind, canonical local target, exact content hash; identifies a completed or potentially completed local publication without claiming directory durability |
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
| `HubReleaseDescriptorPayload` | target-controlled release ID, immutable manifest/platform digests, provenance, runtime/API/schema contracts, compatibility sets, capabilities, and health assertions; no signature-envelope or signer-policy field |
| `HubReleaseSignatureEnvelope` | descriptor-payload hash, exact manifest/platform digest set, signer identity, signature algorithm, and detached signature bytes |
| `HubReleaseAuthorization` | descriptor-payload hash, signature-envelope hash, and independently resolved validated signer-policy hash |
| `HubComponentReleaseState` | complete retained Hub release authorization tuple, participant-release-map root, and release generation |
| `AuthoritativeDelta` | closed operation-kind-specific predecessor/successor dimensions and unchanged-dimension assertions |
| `SchemaVersionRef` | exact schema ID and schema version |
| `SchemaFlowRequirement` | exact schema ID and version, producer participant (`local` or `peer`), consumer participant (`local` or `peer`) |
| `CapabilityRequirement` | capability ID, executor participant (`local` or `peer`), required flag |
| `CompatibilityRequirementSet` | format version, independently frozen local and peer contract-version sets, directional schema-flow requirements, capability requirements |
| `FrozenCompatibilityContract` | frozen-format version, canonical local and peer contract-version sets, canonical schema-flow requirements, canonical capability requirements |
| `CompatibilityBlocker` | exact closed blocker code, subject ID, participant, and deterministic detail string; not durable evidence |
| `CompatibilityDecision` | compatible flag, ordered blockers, and exact local and peer contract-version sets considered |
| `CompatibilityReport` | report version, participant (`local` or `peer`), contract versions, exact produced and consumed schema versions, and frozen capability set |
| `SchemaCatalog` | catalog ID, catalog version, ordered schema definitions |
| `SchemaDefinition` | schema ID, schema version, object kind, exact required and optional field names, allowed destination schema-version references |
| `SchemaValidationResult` | schema ID, schema version, valid flag, deterministic violation strings |
| `SchemaTransitionDecision` | compatible flag and ordered `CompatibilityBlocker` values |
| `CapabilitySet` | participant, capability contract version, ordered capability IDs |
| `FrozenCapabilitySet` | participant, capability contract version, deterministic unique capability IDs |
| `CapabilityDecision` | allowed flag and ordered `CompatibilityBlocker` values |

Every cross-module value MUST use these models or a versioned schema-owned
model. Bare dictionaries MUST NOT cross a public module boundary.

#### 4.1.1 Wave 1C negotiation types, ownership, and wire contracts

`MOTHER-OFM-CORE-001` is the definition, constructor-validation,
serialization, and export owner for every Wave 1C dataclass that crosses a
module boundary:

```text
SchemaVersionRef
SchemaFlowRequirement
CapabilityRequirement
CompatibilityRequirementSet
FrozenCompatibilityContract
CompatibilityBlocker
CompatibilityDecision
CompatibilityReport
SchemaCatalog
SchemaDefinition
SchemaValidationResult
SchemaTransitionDecision
CapabilitySet
FrozenCapabilitySet
CapabilityDecision
```

All names above MUST be exported from `tools.mother.common.models`. CORE-006
owns schema catalog, object-validation, and transition-validation semantics.
CORE-007 owns capability decoding, ambiguity rejection, freezing, and
requirement semantics. CORE-010 owns compatibility-report decoding, contract
freezing, and local/peer comparison semantics. Semantic ownership does not move
the shared type definitions out of CORE-001.

The exact Python fields are normative:

| Type | Exact fields in declaration order |
|---|---|
| `SchemaVersionRef` | `schema_id: str`, `schema_version: str` |
| `SchemaFlowRequirement` | `schema_id: str`, `schema_version: str`, `producer: str`, `consumer: str` |
| `CapabilityRequirement` | `capability_id: str`, `executor: str`, `required: bool` |
| `CompatibilityRequirementSet` | `format_version: str`, `local_contract_versions: tuple[str, ...]`, `peer_contract_versions: tuple[str, ...]`, `schema_flows: tuple[SchemaFlowRequirement, ...]`, `capability_requirements: tuple[CapabilityRequirement, ...]` |
| `FrozenCompatibilityContract` | `format_version: str`, `local_contract_versions: tuple[str, ...]`, `peer_contract_versions: tuple[str, ...]`, `schema_flows: tuple[SchemaFlowRequirement, ...]`, `capability_requirements: tuple[CapabilityRequirement, ...]` |
| `CompatibilityBlocker` | `code: str`, `subject_id: str`, `participant: str`, `detail: str` |
| `CompatibilityDecision` | `compatible: bool`, `blockers: tuple[CompatibilityBlocker, ...]`, `local_contract_versions: tuple[str, ...]`, `peer_contract_versions: tuple[str, ...]` |
| `CompatibilityReport` | `report_version: str`, `participant: str`, `contract_versions: tuple[str, ...]`, `produced_schemas: tuple[SchemaVersionRef, ...]`, `consumed_schemas: tuple[SchemaVersionRef, ...]`, `capabilities: FrozenCapabilitySet` |
| `SchemaCatalog` | `catalog_id: str`, `catalog_version: str`, `schemas: tuple[SchemaDefinition, ...]` |
| `SchemaDefinition` | `schema_id: str`, `schema_version: str`, `object_kind: str`, `required_field_names: tuple[str, ...]`, `optional_field_names: tuple[str, ...]`, `allowed_destinations: tuple[SchemaVersionRef, ...]` |
| `SchemaValidationResult` | `schema_id: str`, `schema_version: str`, `valid: bool`, `violations: tuple[str, ...]` |
| `SchemaTransitionDecision` | `compatible: bool`, `blockers: tuple[CompatibilityBlocker, ...]` |
| `CapabilitySet` | `participant: str`, `contract_version: str`, `capabilities: tuple[str, ...]` |
| `FrozenCapabilitySet` | `participant: str`, `contract_version: str`, `capabilities: tuple[str, ...]` |
| `CapabilityDecision` | `allowed: bool`, `blockers: tuple[CompatibilityBlocker, ...]` |

`participant`, `producer`, `consumer`, and `executor` use the closed values
`local` and `peer`. `SchemaFlowRequirement.producer` and `.consumer` MUST be
different. All identifiers and versions are non-empty exact strings. Tuples are
immutable; unordered sets and bare mappings are forbidden at public boundaries.

The supported Wave 1C format versions are exactly:

```text
schema-catalog.v1
capabilities.v1
compatibility-report.v1
compatibility-requirements.v1
frozen-compatibility-contract.v1
```

Component contract versions inside `local_contract_versions`,
`peer_contract_versions`, or `CompatibilityReport.contract_versions` are
opaque non-empty exact strings. They are frozen independently for each
participant and are not looked up in the format-version closed set.

Every public tuple has one normative total order. Exact strings are compared
by their UTF-8 byte sequence; no locale, case folding, or natural-number
ordering is permitted.

| Tuple | Canonical sort key | Duplicate identity key |
|---|---|---|
| local, peer, or report contract versions | exact version string | exact version string |
| produced, consumed, or allowed-destination schema references | `(schema_id, schema_version)` | `(schema_id, schema_version)` |
| schema-flow requirements | `(schema_id, schema_version, producer, consumer)` | `(schema_id, schema_version, producer, consumer)` |
| capability requirements | `(capability_id, executor)` | `(capability_id, executor)`; differing `required` values for the same identity are conflicting duplicates |
| capability IDs | exact capability ID | exact capability ID |
| schema required or optional field names | exact field name | exact field name |
| schema definitions in a catalog | `(schema_id, schema_version)` | `(schema_id, schema_version)` |

Decoders reject duplicate identities before sorting. Supported decoded values
and all freeze operations return tuples in the order above.
`freeze_contract_versions` canonicalizes local and peer contract versions,
schema flows, and capability requirements independently. A compatibility
report canonicalizes its contract versions, produced schema references, and
consumed schema references independently.

CORE-001 generic model serialization is also normative for these fifteen
types: `serialize_model` emits `schema_version=1` followed by the exact Python
fields in declaration order, recursively serializes nested CORE-001 values,
and `deserialize_model` round-trips only the exact declared type and field set.

The public decoding boundary is canonical JSON bytes in and immutable
CORE-001 values out. The exact serialized envelopes are:

```text
SchemaCatalog:
  catalog_id
  catalog_version = schema-catalog.v1
  schemas[]
    allowed_destinations[] {schema_id, schema_version}
    object_kind
    optional_field_names[]
    required_field_names[]
    schema_id
    schema_version

CapabilitySet/FrozenCapabilitySet:
  capabilities[]
  contract_version = capabilities.v1
  participant = local | peer

CompatibilityReport payload:
  consumed_schemas[] {schema_id, schema_version}
  contract_versions[]
  participant = local | peer
  produced_schemas[] {schema_id, schema_version}
  report_version = compatibility-report.v1
```

The compatibility-report payload does not duplicate capability bytes.
`decode_compatibility_report` receives the separately decoded
`FrozenCapabilitySet`, requires its participant to equal the report
participant, and attaches that typed value to the returned
`CompatibilityReport`.

Decoders validate in this exact order:

1. Require `bytes`, strict UTF-8 JSON, a top-level object, and byte-for-byte
   canonical CORE-003 JSON. Failure is `MOTHER_SCHEMA_MALFORMED_BYTES`.
2. Read the envelope discriminator before validating any other field:
   `catalog_version`, `contract_version`, or `report_version`. A missing or
   non-string discriminator is `MOTHER_SCHEMA_MALFORMED_OBJECT`; a string not
   in the exact supported set is `MOTHER_SCHEMA_UNKNOWN_VERSION`, even when
   other envelope fields are missing or malformed.
3. For a supported version, require the exact serialized field set, exact
   primitive types, and closed participant values. Failure is
   `MOTHER_SCHEMA_MALFORMED_OBJECT`.
4. Reject duplicate or conflicting schema, contract-version, destination, or
   capability identities with `MOTHER_SCHEMA_AMBIGUOUS_DECLARATION`.
5. Construct and return the immutable CORE-001 value. No implicit coercion,
   nearby-version substitution, field dropping, or field defaulting is allowed.

`SchemaValidationResult.violations` uses only
`missing-required-field:<field-name>` and `unknown-field:<field-name>`.
Violations are ordered first by code (`missing-required-field` before
`unknown-field`) and then by field name. An interpretable object with no
violations returns `valid=True`.

The closed `CompatibilityBlocker.code` set and global ordering are:

```text
1 contract-version-set-changed
2 schema-producer-unsupported
3 schema-consumer-unsupported
4 required-capability-absent
5 schema-transition-requirement-mismatch
6 schema-transition-undeclared
```

Blockers are ordered by that rank, then `subject_id`, `participant`, and
`detail`. Exact blocker construction is:

| Code | Subject | Participant | Detail |
|---|---|---|---|
| `contract-version-set-changed` | `contract_versions` | changed report participant | `expected=<comma-joined canonical set>;observed=<comma-joined canonical set>` |
| `schema-producer-unsupported` | `<schema_id>@<schema_version>` | declared producer | `required producer schema is absent` |
| `schema-consumer-unsupported` | `<schema_id>@<schema_version>` | declared consumer | `required consumer schema is absent` |
| `required-capability-absent` | capability ID | declared executor | `required capability is absent` |
| `schema-transition-requirement-mismatch` | requirement `<schema_id>@<schema_version>` | declared producer | `requirement=<schema_id>@<schema_version>;destination=<schema_id>@<schema_version>;transition=requirement-mismatch` |
| `schema-transition-undeclared` | destination `<schema_id>@<schema_version>` | declared producer | `source=<schema_id>@<schema_version>;destination=<schema_id>@<schema_version>;transition=undeclared` |

Schema flow is exact and directional. For `peer` → `local`, the peer report
MUST contain the exact schema ID/version in `produced_schemas` and the local
report MUST contain it in `consumed_schemas`. For `local` → `peer`, the local
report MUST contain the exact schema ID/version in `produced_schemas` and the
peer report MUST contain it in `consumed_schemas`.

A required capability blocks only the participant named by `executor`.
Optional capabilities never block. CORE-007 and CORE-010 use the same exact
`required-capability-absent` blocker.

Local and peer component contract-version strings need not match.
`freeze_contract_versions` independently canonicalizes the expected local and
peer sets. `check_peer_compatibility` compares each report only with its own
frozen expected set and proves compatibility through the exact schema-flow and
required-capability checks. A changed but interpretable participant set returns
`contract-version-set-changed`; it is not an unknown-format error. Full
operation compatibility requires both the capability decision and the peer
compatibility decision to be positive.

CORE-006, CORE-007, and CORE-010 are pure readers for Wave 1C. They MUST NOT
acquire locks, write files, publish object-store content, create `EvidenceRef`
values, hash validated objects as an output obligation, or dispatch external
calls. CORE-008 exclusively owns durable evidence storage and `EvidenceRef`
creation.


#### 4.1.2 CORE-008 evidence and CORE-009 reporting contracts

CORE-008 and CORE-009 own versioned module-specific immutable types. These
types are not registry entries and do not move authority from the governing
documents. They are exported from their owning modules:

```text
tools.mother.common.evidence:
  EvidenceDocument
  RedactionRule
  RedactionPolicy
  EvidenceExportRequest
  EvidenceManifestEntry
  EvidenceManifest
  EvidenceExportResult

tools.mother.common.reporting:
  AllowedCommand
  AllowedCommandsReport
  EvidenceReport
  ReportArtifactRef
```

The exact Python fields are normative:

| Type | Exact fields in declaration order |
|---|---|
| `EvidenceDocument` | `document_version: str`, `schema_id: str`, `source: str`, `observation_time: str`, `redaction_policy: str`, `payload: bytes` |
| `RedactionRule` | `json_pointer: str` |
| `RedactionPolicy` | `policy_version: str`, `policy_id: str`, `rules: tuple[RedactionRule, ...]` |
| `EvidenceExportRequest` | `source_ref: EvidenceRef`, `policy: RedactionPolicy` |
| `EvidenceManifestEntry` | `source_ref: EvidenceRef`, `export_ref: EvidenceRef` |
| `EvidenceManifest` | `manifest_version: str`, `entries: tuple[EvidenceManifestEntry, ...]` |
| `EvidenceExportResult` | `manifest: EvidenceManifest`, `manifest_ref: EvidenceRef`, `exported_refs: tuple[EvidenceRef, ...]` |
| `AllowedCommand` | `command: str`, `reason: str` |
| `AllowedCommandsReport` | `report_version: str`, `operation_id: str`, `classification: str`, `active_operation_id: str | None`, `commands: tuple[AllowedCommand, ...]` |
| `EvidenceReport` | `report_version: str`, `operation_id: str`, `manifest_ref: EvidenceRef`, `evidence_refs: tuple[EvidenceRef, ...]` |
| `ReportArtifactRef` | `format: str`, `relative_name: str`, `content_hash: ContentHash`, `byte_length: int` |

All are frozen dataclasses with slots. Identifiers, schema names, sources,
timestamps, policies, commands, reasons, classifications, relative names, and
format values are non-empty exact strings. Every wire-facing string MUST
already be Unicode Normalization Form C (NFC), defined by
`unicodedata.normalize("NFC", value) == value`. Constructors reject non-NFC
string fields. Public module boundaries also reject non-NFC strings nested in
accepted `EvidenceRef` metadata, payload object keys or string values, decoded
JSON Pointer tokens, commands, reasons, and directly supplied report models.
No boundary silently normalizes caller input. Duplicate detection and UTF-8
ordering occur only after NFC validation.

Public collections are tuples only; mappings, lists, sets, and frozensets are
rejected at constructors. Every tuple member has the exact declared type.
`EvidenceDocument.payload` is bytes, not `bytearray`, `memoryview`, text, or
another bytes-like substitute. `byte_length` is a non-negative integer and
booleans are rejected. `ReportArtifactRef.format` is exactly `json`, `text`, or
`allowed-commands`.

The supported format versions are exactly:

```text
evidence-document.v1
redaction-policy.v1
evidence-manifest.v1
allowed-commands-report.v1
evidence-report.v1
```

Constructors reject every other version. `AllowedCommandsReport.classification`
is exactly one of:

```text
local-current
local-stale-network-agrees
network-replica-mismatch
wedged
```

`EvidenceDocument.payload` is canonical CORE-003 JSON bytes for one top-level
object. Every decoded object key and string value is already NFC; canonical
equivalence is not accepted as byte equivalence. It is not arbitrary bytes and
it never contains private-state file bytes. The storage envelope is canonical
JSON with the exact serialized fields:

```text
document_version
observation_time
payload
redaction_policy
schema_id
source
```

`payload` is embedded as its decoded JSON object. `load_evidence` reconstructs
the exact canonical payload bytes. No bare mapping crosses the API.

The shared wire objects used by CORE-008 and CORE-009 are exact:

```text
ContentHash:
  schema_version: 1
  algorithm: "sha256"
  digest: <64 lowercase hexadecimal characters>

EvidenceRef:
  schema_version: 1
  object_hash: <ContentHash wire object>
  schema: <non-empty string>
  redaction_policy: <non-empty string>
  source: <non-empty string>
  observation_time: <non-empty string>
```

No extra or missing wire field is accepted. Object keys are emitted by
CORE-003 canonical JSON ordering.

The public signatures are exactly:

```python
# tools.mother.common.evidence
def store_evidence(
    root: Path,
    document: EvidenceDocument,
    *,
    operation: OperationIdentity,
) -> EvidenceRef: ...

def load_evidence(
    root: Path,
    reference: EvidenceRef,
    *,
    operation: OperationIdentity,
) -> EvidenceDocument: ...

def redact_copy(
    document: EvidenceDocument,
    policy: RedactionPolicy,
    *,
    operation: OperationIdentity,
) -> EvidenceDocument: ...

def export_manifest(
    source_root: Path,
    export_root: Path,
    requests: tuple[EvidenceExportRequest, ...],
    manifest_time: str,
    *,
    operation: OperationIdentity,
) -> EvidenceExportResult: ...

def load_export_result(
    export_root: Path,
    manifest_ref: EvidenceRef,
    *,
    operation: OperationIdentity,
) -> EvidenceExportResult: ...

# tools.mother.common.reporting
def build_evidence_report(
    export_root: Path,
    manifest_ref: EvidenceRef,
    *,
    operation: OperationIdentity,
) -> EvidenceReport: ...

def build_allowed_commands_report(
    classification: str,
    active_operation_id: str | None,
    commands: tuple[AllowedCommand, ...],
    *,
    operation: OperationIdentity,
) -> AllowedCommandsReport: ...

def render_json(
    root: Path,
    report: EvidenceReport | AllowedCommandsReport,
    *,
    operation: OperationIdentity,
) -> ReportArtifactRef: ...

def render_text(
    root: Path,
    report: EvidenceReport | AllowedCommandsReport,
    *,
    operation: OperationIdentity,
) -> ReportArtifactRef: ...

def render_allowed_commands(
    root: Path,
    report: AllowedCommandsReport,
    *,
    operation: OperationIdentity,
) -> ReportArtifactRef: ...
```

CORE-008 accepts only exact `Path` roots already resolved and contained by
CORE-005. `source_root` is a verified source object-store root. `export_root`
is a distinct verified export object-store root; equal, ancestor, or descendant
roots are rejected before any read or write. CORE-008 delegates
content-addressed publication and verified reads to CORE-012 and does not
reimplement object-store paths or atomic publication.

`store_evidence` validates exact types, non-empty fields, NFC metadata, NFC
payload keys and string values, and canonical payload bytes before it
canonicalizes the storage envelope and publishes it once. Non-NFC document
metadata or payload strings are `MOTHER_EVIDENCE_MALFORMED_DOCUMENT`;
non-NFC accepted reference metadata is
`MOTHER_EVIDENCE_REFERENCE_MISMATCH`; and non-NFC policy IDs, pointer strings,
or decoded pointer tokens are `MOTHER_EVIDENCE_REDACTION_FAILED`. It returns an
`EvidenceRef` whose `schema`, `redaction_policy`, `source`, and
`observation_time` exactly equal the corresponding document fields. The call is
idempotent for the same document. `load_evidence` rejects non-NFC reference
metadata before reading, rehashes through CORE-012, decodes the exact envelope,
and requires every reference metadata field to match the stored document.

Redaction uses RFC 6901 JSON Pointer syntax with this exact normalization:

1. the empty pointer is forbidden and every pointer begins with `/`;
2. split the pointer at `/` boundaries without decoding first;
3. decode each token from left to right, replacing `~1` with `/` and `~0` with
   `~`; any `~` not followed by `0` or `1` is invalid;
4. require the raw pointer string and every decoded token to already be NFC;
5. re-encode each decoded token by replacing `~` with `~0` and `/` with `~1`,
   then join the tokens with `/`;
6. compare duplicates using the re-encoded normalized pointer;
7. reject overlap when either decoded token sequence is a prefix of the other,
   including exact equality;
8. while resolving an array, a token is a canonical non-negative decimal index
   with no leading zero except `0`;
9. every pointer resolves against the original unmodified payload;
10. each selected value is replaced with the exact string `[REDACTED]`;
11. the input document and policy remain unchanged.

Because overlapping pointers are rejected, replacement order cannot change the
result. Non-overlapping normalized pointers are processed in UTF-8 byte order.
`redact_copy` returns a new `EvidenceDocument` whose `redaction_policy` is the
policy ID and whose payload is canonical JSON bytes. It does not store the copy.

The closed private-material key set is:

```text
access_token
api_token
credential
mnemonic
password
private_key
private_key_bytes
refresh_token
secret
secret_bytes
seed
```

`signature_bytes` is intentionally not private material; detached signatures
are public verification evidence. Before export, every occurrence of a closed
private-material key at any nesting depth MUST have the exact string value
`[REDACTED]`.

`export_manifest` accepts only requests. For every request it MUST:

1. load the exact source object from `source_root` through `load_evidence` using
   the complete `source_ref`, including its object hash;
2. apply the request's policy itself through `redact_copy`;
3. reject redaction policy `none` or any surviving closed private-material key;
4. store the redacted document in `export_root`;
5. record the exact source and resulting export references.

A caller cannot supply replacement document bytes to `export_manifest`.
Matching metadata never substitutes for the source object hash. Duplicate
complete source references are prohibited. Distinct source references MAY
produce one identical redacted export reference.

The durable manifest payload is canonical JSON with this exact object:

```text
{
  "entries": [
    {
      "export_ref": <EvidenceRef wire object>,
      "source_ref": <EvidenceRef wire object>
    }
  ],
  "manifest_version": "evidence-manifest.v1"
}
```

The manifest is stored as an `EvidenceDocument` with exactly:

```text
document_version: evidence-document.v1
schema_id: mother.evidence-manifest.v1
source: MOTHER-OFM-CORE-008.export_manifest
observation_time: <manifest_time argument>
redaction_policy: manifest
payload: <canonical manifest payload bytes>
```

The returned `manifest_ref` is the reference returned by `store_evidence` for
that exact document.

`load_export_result` is the restart seam. It loads the exact manifest document
from `export_root`, requires the manifest reference metadata above, decodes the
exact canonical manifest payload and version, rejects malformed, unsorted, or
duplicate source entries, and verifies every unique exported reference through
`load_evidence(export_root, export_ref, ...)`. Every verified export MUST have
a redaction policy other than `none`, and its payload MUST contain no surviving
closed private-material key whose value differs from `[REDACTED]`.
`load_export_result` reconstructs the exact typed `EvidenceExportResult` and
does not require the source store to remain mounted. A syntactically valid
manually stored manifest cannot bless an unredacted or secret-bearing export.

Evidence tuples use these total orders and duplicate identities:

| Tuple | Canonical sort key | Duplicate identity |
|---|---|---|
| redaction rules | normalized JSON pointer | normalized JSON pointer or overlapping token path |
| export requests | source object-hash digest, schema, redaction policy, source, observation time | complete `source_ref` |
| manifest entries | source object-hash digest, source schema, source redaction policy, source, source observation time | complete `source_ref` |
| exported references | object-hash digest, schema, redaction policy, source, observation time | complete `EvidenceRef` |
| evidence-report references | object-hash digest, schema, redaction policy, source, observation time | complete `EvidenceRef` |
| allowed commands | command, reason | exact command; different reasons for one command are conflicting duplicates |

String ordering is UTF-8 byte order. Decoders and builders reject prohibited
duplicates before sorting. Manifest entries are sorted only by source
reference. `EvidenceExportResult.exported_refs` is the unique canonical
export-reference set derived from the manifest entries; its order is
independent of manifest-entry order.

`build_evidence_report` takes only an export object-store root and a durable
manifest reference. It calls
`MOTHER-OFM-CORE-008.load_export_result(export_root, manifest_ref, ...)` and
therefore binds report provenance to the exact stored manifest object and to
the verified exported objects named by that manifest. It MUST NOT accept a
caller-supplied `EvidenceManifest` or `EvidenceExportResult`. It copies the
operation ID from the real `OperationIdentity`, requires the recovered unique
manifest export-reference set and `exported_refs` to be equal, canonicalizes
the evidence references, and produces `evidence-report.v1`. A fabricated
manifest reference, unrelated typed manifest, missing export, unredacted
export, or secret-bearing export cannot produce a report.

`build_allowed_commands_report` accepts only the closed classifications above,
copies the operation ID, canonicalizes commands, and preserves the supplied
active operation ID. It renders a supplied legal command set; it does not
invent authority or decide operation legality independently of the
classification and operation-control owners.

A command or reason contains secret-shaped material exactly when this
case-insensitive regular expression matches:

```text
(?:^|[\s?&;,])(?:--)?(?:access[-_]?token|api[-_]?token|credential|mnemonic|password|private[-_]?key(?:[-_]?bytes)?|refresh[-_]?token|secret(?:[-_]?bytes)?|seed|token)\s*[:=]\s*\S+
```

Builders and renderers reject such material with
`MOTHER_REPORT_PRIVATE_MATERIAL` before any file write. Non-NFC report fields,
accepted evidence-reference metadata, or directly supplied nested values are
`MOTHER_REPORT_MALFORMED_MODEL`.

CORE-009 owns only derived report files. The `root` argument is the exact
CORE-005-resolved `reports/<operation-id>/` directory. Its final path component,
the report's `operation_id`, and the supplied `OperationIdentity.operation_id`
MUST all be equal. The exact filenames are:

```text
evidence-report.json
evidence-report.txt
allowed-commands-report.json
allowed-commands-report.txt
allowed-commands.txt
```

The exact JSON report wire objects are:

```text
AllowedCommand:
  command: <non-empty string>
  reason: <non-empty string>

AllowedCommandsReport:
  report_version: "allowed-commands-report.v1"
  operation_id: <non-empty string>
  classification: <closed classification>
  active_operation_id: <non-empty string or null>
  commands: [<AllowedCommand wire object>, ...]

EvidenceReport:
  report_version: "evidence-report.v1"
  operation_id: <non-empty string>
  manifest_ref: <EvidenceRef wire object>
  evidence_refs: [<EvidenceRef wire object>, ...]
```

`render_json` writes the corresponding exact object using CORE-003 canonical
JSON, with no byte-order mark and no trailing newline.

`render_text` writes UTF-8 with LF line endings. Every string atom after a label
is encoded as one CORE-003 canonical JSON string; `null` is emitted literally.
The exact allowed-commands report lines are:

```text
report_version<TAB><JSON string><LF>
operation_id<TAB><JSON string><LF>
classification<TAB><JSON string><LF>
active_operation_id<TAB><JSON string or null><LF>
command<TAB><JSON command string><TAB><JSON reason string><LF>
```

There is one `command` line per canonical command. The exact evidence-report
lines are:

```text
report_version<TAB><JSON string><LF>
operation_id<TAB><JSON string><LF>
manifest_ref<TAB><canonical JSON EvidenceRef object><LF>
evidence_ref<TAB><canonical JSON EvidenceRef object><LF>
```

There is one `evidence_ref` line per canonical evidence reference. There are no
other labels, blank lines, spaces around tabs, or platform-specific line
endings. Each non-empty text report therefore ends with one LF.

`render_allowed_commands` writes exactly one
`<JSON command string><TAB><JSON reason string><LF>` line per canonical command.
An empty command set writes zero bytes.

Every renderer validates the complete directly supplied model before writing,
including NFC strings, tuple member types, duplicate commands or evidence
references, secret-shaped text, and exact `EvidenceRef` metadata. An
`EvidenceReport.manifest_ref` MUST have schema
`mother.evidence-manifest.v1`, redaction policy `manifest`, source
`MOTHER-OFM-CORE-008.export_manifest`, non-empty NFC observation time, and a
valid SHA-256 hash. Renderers use CORE-011 `durable_replace` and return a
`ReportArtifactRef` whose
`relative_name` and `format` match the selected renderer, whose `byte_length`
equals the exact written-byte length, and whose `content_hash` equals
CORE-004 SHA-256 of the exact written bytes. Re-rendering identical input is
byte-identical.

CORE-008 MAY write only immutable evidence objects and manifests. CORE-009 MAY
write only derived reports. Neither module acquires logical operation scopes,
writes journals, changes state pointers, creates authority objects, dispatches
external calls, or treats reports as authority. CORE-009 creates no
`EvidenceRef`. CORE-008 creates no report file.

A delegated CORE-012 `MotherError` is propagated without changing its code,
module ID, retry class, authority effect, durable-effect references, evidence
references, or allowed next actions. In particular,
`immutable-object-publication` remains `immutable-object-publication`. A
delegated CORE-011 `MotherError` is preserved in the same way, and
`local-file-publication` remains `local-file-publication`. CORE-008 and
CORE-009 do not introduce new `DurableEffectRef.effect_kind` values.



#### 4.1.3 STATE-001 journal and STATE-002 checkpoint contracts

`MOTHER-OFM-STATE-001` owns the canonical journal envelope, immutable journal
entry parsing and construction, stable-head observation, lineage loading, and
deterministic replay. `MOTHER-OFM-STATE-002` owns checkpoint payload
construction, checkpoint selection and validation, and verification of the
checkpoint state-object closure. Checkpoints remain journal-entry payloads; they
do not gain a second mutable head or a separate authority path.

The module-owned immutable types and protocols are exported exactly from:

```text
tools.mother.common.journal:
  JournalEntryRef
  JournalEntry
  JournalEntryBuildRequest
  LoadedAuthorizationBundle
  JournalLineageMember
  JournalLineage
  ValidatedJournalLineage
  AuthorizedJournalLineage
  CheckpointReplayProof
  JournalReplayInput
  JournalReplayResult
  JournalReducer
  AuthorizationBundleValidator

tools.mother.common.checkpoints:
  CheckpointBuildRequest
  CheckpointEntryBuildRequest
  CheckpointPayload
  CheckpointBuildResult
  CheckpointEntryBuildResult
  CheckpointSelection
  CheckpointValidationResult
  StateClosureEdge
  StateClosureManifest
  StateClosureManifestBuildResult
  StateClosure
```

`JournalReducer` and `AuthorizationBundleValidator` are runtime-checkable
protocols rather than dataclasses. Every other name above is a frozen dataclass
with slots.

The following six values are proof-bearing and use module-controlled
construction:

```text
ValidatedJournalLineage
AuthorizedJournalLineage
CheckpointValidationResult
StateClosure
CheckpointReplayProof
JournalReplayInput
```

Each is declared as a frozen, slotted dataclass with `init=False`, inherits a
non-exported module-private proof-seal base, and defines a public `__init__`
that always raises `TypeError`. Its owning private factory allocates with
`object.__new__`, assigns the exact fields with `object.__setattr__`, and sets
the private seal. It is created only by its owning validation or binding seam:

```text
STATE-001.validate_lineage  → ValidatedJournalLineage
STATE-001.authorize_lineage → AuthorizedJournalLineage
STATE-002.validate_checkpoint → CheckpointValidationResult
STATE-002.state_closure → StateClosure
STATE-002.prepare_replay → CheckpointReplayProof + JournalReplayInput
```

Ordinary direct construction of every proof-bearing type MUST raise `TypeError`.
The private factories set a non-exported, process-local proof seal that is not a
dataclass field and is never serialized. Public consumers MUST verify that seal
before reading proof fields, touching object stores, invoking an authorization
validator or reducer, or rereading the stable head. A value fabricated with
`object.__new__`, field assignment, deserialization, copying, or subclassing but
without the exact owning-module seal is rejected as follows:

```text
authorize_lineage(unsealed ValidatedJournalLineage)
  → MOTHER_STATE_INVALID_LINEAGE

validate_checkpoint(unsealed AuthorizedJournalLineage)
  → MOTHER_STATE_CHECKPOINT_INVALID

prepare_replay(unsealed AuthorizedJournalLineage,
               unsealed CheckpointValidationResult,
               or unsealed StateClosure)
  → MOTHER_STATE_REPLAY_FAILED

replay_forward(unsealed JournalReplayInput,
               unsealed nested AuthorizedJournalLineage,
               or unsealed CheckpointReplayProof)
  → MOTHER_STATE_REPLAY_FAILED
```

These checks prevent ordinary callers from replacing structural validation,
AUTH-003 validation, checkpoint validation, or durable closure verification with
mutually consistent fabricated field values. The exact public dataclass fields
remain normative:

| Type | Exact fields in declaration order |
|---|---|
| `JournalEntryRef` | `journal_id: str`, `sequence: int`, `entry_hash: ContentHash`, `authorization_bundle_hash: ContentHash`, `state_hash: ContentHash` |
| `JournalEntry` | `entry_version: str`, `journal_id: str`, `network: str`, `sequence: int`, `operation_id: str`, `operation_kind: str`, `previous_entry_hash: ContentHash | None`, `previous_authorization_bundle_hash: ContentHash | None`, `previous_state_hash: ContentHash | None`, `event_type: str`, `event_payload: bytes`, `resulting_state_hash: ContentHash`, `created_at: str` |
| `JournalEntryBuildRequest` | `journal_id: str`, `sequence: int`, `previous: JournalEntryRef | None`, `event_type: str`, `event_payload: bytes`, `resulting_state: bytes`, `created_at: str` |
| `LoadedAuthorizationBundle` | `object_hash: ContentHash`, `payload: bytes` |
| `JournalLineageMember` | `reference: JournalEntryRef`, `entry: JournalEntry`, `authorization_bundle: LoadedAuthorizationBundle` |
| `JournalLineage` | `head: HeadTuple`, `stop: JournalEntryRef`, `members: tuple[JournalLineageMember, ...]` |
| `ValidatedJournalLineage` | `head: HeadTuple`, `stop: JournalEntryRef`, `members: tuple[JournalLineageMember, ...]` |
| `AuthorizedJournalLineage` | `head: HeadTuple`, `stop: JournalEntryRef`, `members: tuple[JournalLineageMember, ...]` |
| `CheckpointReplayProof` | `checkpoint_ref: JournalEntryRef`, `state_schema: str`, `state: bytes`, `state_hash: ContentHash`, `state_closure_manifest_hash: ContentHash`, `state_closure_members: tuple[ContentHash, ...]`, `authoritative: bool` |
| `JournalReplayInput` | `lineage: AuthorizedJournalLineage`, `checkpoint: CheckpointReplayProof` |
| `JournalReplayResult` | `head: HeadTuple`, `checkpoint_ref: JournalEntryRef`, `state_schema: str`, `state: bytes`, `state_hash: ContentHash`, `applied_entry_refs: tuple[JournalEntryRef, ...]` |
| `CheckpointBuildRequest` | `checkpoint_kind: str`, `covers_through: JournalEntryRef | None`, `state_schema: str`, `state: bytes`, `state_object_refs: tuple[ContentHash, ...]`, `state_closure_manifest_hash: ContentHash`, `prepared_intent_hash: ContentHash | None`, `superseded_lineage_heads: tuple[ContentHash, ...]` |
| `CheckpointEntryBuildRequest` | `journal_id: str`, `sequence: int`, `previous: JournalEntryRef | None`, `checkpoint_request: CheckpointBuildRequest`, `created_at: str` |
| `CheckpointPayload` | `checkpoint_version: str`, `checkpoint_kind: str`, `covers_through_sequence: int`, `covers_through_entry_hash: ContentHash | None`, `state_schema: str`, `state: bytes`, `state_hash: ContentHash`, `state_object_refs: tuple[ContentHash, ...]`, `state_closure_manifest_hash: ContentHash`, `prepared_intent_hash: ContentHash | None`, `superseded_lineage_heads: tuple[ContentHash, ...]` |
| `CheckpointBuildResult` | `checkpoint: CheckpointPayload`, `event_payload: bytes` |
| `CheckpointEntryBuildResult` | `checkpoint: CheckpointPayload`, `event_payload: bytes`, `entry_bytes: bytes` |
| `CheckpointSelection` | `checkpoint_ref: JournalEntryRef`, `checkpoint: CheckpointPayload`, `later_entry_refs: tuple[JournalEntryRef, ...]` |
| `CheckpointValidationResult` | `checkpoint_ref: JournalEntryRef`, `checkpoint: CheckpointPayload`, `authoritative: bool` |
| `StateClosureEdge` | `parent: ContentHash`, `children: tuple[ContentHash, ...]` |
| `StateClosureManifest` | `manifest_version: str`, `roots: tuple[ContentHash, ...]`, `edges: tuple[StateClosureEdge, ...]` |
| `StateClosureManifestBuildResult` | `manifest: StateClosureManifest`, `manifest_bytes: bytes`, `manifest_hash: ContentHash` |
| `StateClosure` | `manifest_hash: ContentHash`, `roots: tuple[ContentHash, ...]`, `edges: tuple[StateClosureEdge, ...]`, `members: tuple[ContentHash, ...]` |

All strings are non-empty and already NFC. Integers are exact `int` values;
booleans are rejected. Sequences are positive. Epochs and coverage sequences are
non-negative. Public collections are tuples only, and every tuple member has
the exact declared type. Bare mappings, lists, sets, frozensets, `bytearray`,
`memoryview`, and implicit coercion are forbidden at public boundaries.
`event_payload`, `resulting_state`, checkpoint `state`, and loaded object
`payload` values are exact `bytes`.

The closed versions are:

```text
mother.journal.metadata.v1
mother.journal.head.v2
mother.journal.entry.v1
mother.committed-state-projection.v1
mother.journal.checkpoint.v1
mother.state.object.v1
mother.state.closure-manifest.v1
```

The broader parent design recognizes `network`, `action`, and `rollback`
journals, but the exact STATE-001/002 callable contracts in this slice accept
only `journal_kind="network"`. Every `JournalEntryRef`, lineage member, stable
head, and checkpoint selection therefore carries a non-null
`authorization_bundle_hash` and an exact loaded authorization bundle. Action
and rollback journal decoding, null-bundle references, and their typed journal
contexts are deferred and MUST NOT be inferred by these APIs.

The closed checkpoint kinds and their construction constraints are:

| Checkpoint kind | Coverage | Prospective containing entry | Prepared intent | Superseded heads |
|---|---|---|---|---|
| `initial` | `covers_through=None` | sequence `1`, `previous=None` | `None` | empty |
| `initial-network-birth` | `covers_through=None` | sequence `1`, `previous=None` | required | empty |
| `routine` | exact immediately preceding committed entry | sequence is coverage sequence plus one; `previous` equals coverage | `None` | empty |
| `authoritative-rectification` | exact selected predecessor entry | sequence is coverage sequence plus one; `previous` equals coverage | required | non-empty |

`CheckpointPayload.covers_through_sequence` is `0` and
`covers_through_entry_hash` is `None` when `covers_through=None`. Otherwise the
two fields are copied exactly from `CheckpointBuildRequest.covers_through`.

`build_checkpoint` performs only intrinsic payload validation and
construction-time replay validation. For `routine`, `prior_replay` is mandatory
and MUST end at the exact coverage reference with byte-identical state, state
schema, and state hash. For `authoritative-rectification`, `prior_replay` proves
the selected predecessor and coverage; the checkpoint state MAY differ because
the parent D029 contract authorizes explicit replacement state. Initial kinds
require no prior replay.

`build_checkpoint_entry_bytes` is the sole construction seam that binds a
checkpoint payload to its prospective containing entry. It enforces the exact
sequence and predecessor column above, passes the checkpoint bytes to
`STATE-001.build_entry_bytes` with `event_type="state-checkpoint"`, and passes
the checkpoint state as the exact `resulting_state`. It returns entry bytes
only; no entry hash, authorization bundle, or committed reference exists yet.
`validate_checkpoint` is reserved for an already loaded authorized lineage. It
MUST NOT appear in a pre-publication construction chain.

Committed-read validation never requires archived replay before the checkpoint.
It verifies the intrinsic payload, state hash, existing containing entry,
coverage adjacency, semantically validated committed authorization bundle, and
kind-specific predecessor and resulting-state bindings. This separation permits
routine reopening after older covered entries have been archived.

Every public tuple has one total order. Content hashes are ordered by
`(algorithm, digest)`, with the algorithm compared first by UTF-8 bytes.
`JournalLineage.members`, `ValidatedJournalLineage.members`, and
`AuthorizedJournalLineage.members` are ordered from the committed head toward
the selected checkpoint by strictly descending sequence.
`CheckpointSelection.later_entry_refs` and
`JournalReplayResult.applied_entry_refs` are ordered from the entry after the
checkpoint through the committed head by strictly ascending sequence.
`state_object_refs`, `superseded_lineage_heads`, closure roots, closure members,
and `CheckpointReplayProof.state_closure_members` are unique canonical hash
sets. Closure edges are ordered by parent hash; each edge's children are a
unique canonical hash set. Duplicate entry hashes, bundle hashes, sequence
identities, closure parents, roots, children, or members are rejected before
ordering.

The shared `ContentHash` wire object is the exact object defined in section
4.1.2. The exact journal metadata bytes are CORE-003 canonical JSON for:

```text
{
  "created_at": <non-empty NFC string>,
  "journal_id": <non-empty NFC string>,
  "journal_kind": "network" | "action" | "rollback",
  "schema": "mother.journal.metadata.v1",
  "state_schema": <non-empty NFC string>
}
```

The exact authoritative network-head bytes are CORE-003 canonical JSON for:

```text
{
  "authorization_bundle_hash": <ContentHash wire object>,
  "committed_at": <non-empty NFC string>,
  "head_entry_hash": <ContentHash wire object>,
  "head_epoch": <non-negative integer>,
  "head_id": <non-empty NFC string>,
  "head_sequence": <positive integer>,
  "head_state_hash": <ContentHash wire object>,
  "journal_id": <non-empty NFC string>,
  "schema": "mother.journal.head.v2"
}
```

The exact `committed-state.json` projection envelope is:

```text
{
  "head": {
    "authorization_bundle_hash": <ContentHash wire object>,
    "entry_hash": <ContentHash wire object>,
    "head_epoch": <non-negative integer>,
    "head_id": <non-empty NFC string>,
    "journal_identity": <non-empty NFC string>,
    "sequence": <positive integer>,
    "state_hash": <ContentHash wire object>
  },
  "projection_version": "mother.committed-state-projection.v1",
  "state": <one canonical JSON object>,
  "state_schema": <non-empty NFC string>
}
```

`head.state_hash` is SHA-256 of the CORE-003 canonical bytes of the decoded
`state` object. The projection envelope itself is not included in that state
digest. The seven `head` fields decode directly to `HeadTuple` and MUST equal
the journal-head fields exactly.

The exact immutable entry bytes produced and accepted by STATE-001 are:

```text
{
  "created_at": <non-empty NFC string>,
  "entry_version": "mother.journal.entry.v1",
  "event_payload": <one decoded canonical JSON object>,
  "event_type": <non-empty NFC string>,
  "journal_id": <non-empty NFC string>,
  "network": <non-empty NFC string>,
  "operation_id": <non-empty NFC string>,
  "operation_kind": <closed OperationIdentity operation kind>,
  "previous_authorization_bundle_hash": <ContentHash wire object or null>,
  "previous_entry_hash": <ContentHash wire object or null>,
  "previous_state_hash": <ContentHash wire object or null>,
  "resulting_state_hash": <ContentHash wire object>,
  "sequence": <positive integer>
}
```

`event_payload` and `resulting_state` enter the builder as canonical JSON bytes
for one top-level object. `event_payload` is embedded as its decoded object.
`resulting_state` is not duplicated in an ordinary entry; its exact SHA-256 is
stored as `resulting_state_hash`. The immutable CORE-012 object hash of the
complete entry bytes is the journal entry hash. The illustrative
`entry_hash` field in the parent design is therefore external derived metadata
and is not a self-referential field inside these canonical bytes.

The exact checkpoint event payload bytes are:

```text
{
  "checkpoint_kind": "initial" | "initial-network-birth" | "routine" | "authoritative-rectification",
  "checkpoint_version": "mother.journal.checkpoint.v1",
  "covers_through_entry_hash": <ContentHash wire object or null>,
  "covers_through_sequence": <non-negative integer>,
  "prepared_intent_hash": <ContentHash wire object or null>,
  "state": <one decoded canonical JSON object>,
  "state_closure_manifest_hash": <ContentHash wire object>,
  "state_hash": <ContentHash wire object>,
  "state_object_refs": [<ContentHash wire object>, ...],
  "state_schema": <non-empty NFC string>,
  "superseded_lineage_heads": [<ContentHash wire object>, ...]
}
```

`CheckpointPayload.state_hash` is SHA-256 of the exact canonical state bytes.
`state_closure_manifest_hash` names one immutable manifest built and published
before the checkpoint entry bytes exist. `CheckpointBuildResult.event_payload`
is the canonical JSON encoding of the exact checkpoint payload object.

Every state object reachable from a checkpoint root has exact CORE-003 canonical
JSON bytes:

```text
{
  "object_version": "mother.state.object.v1",
  "references": [<ContentHash wire object>, ...],
  "state_schema": <non-empty NFC string>,
  "value": <one decoded canonical JSON object>
}
```

`references` is the complete direct child-reference set encoded by that object,
ordered as a unique canonical hash set. A state-object schema MUST express every
transitive object reference in this field; hash-shaped strings inside `value`
are data and do not create an edge.

The exact closure-manifest bytes are:

```text
{
  "edges": [
    {
      "children": [<ContentHash wire object>, ...],
      "parent": <ContentHash wire object>
    },
    ...
  ],
  "manifest_version": "mother.state.closure-manifest.v1",
  "roots": [<ContentHash wire object>, ...]
}
```

The manifest contains exactly one edge for every member reachable from `roots`,
including leaves with `children=[]`. Each edge is derived from the exact
`references` field of the verified parent object. The manifest object itself is
metadata and is not a state-closure member.

The exact public signatures are:

```python
# tools.mother.common.journal
def read_stable_head(
    paths: NetworkHeadPaths,
    *,
    operation: OperationIdentity,
) -> HeadTuple: ...

def load_entry(
    entry_root: Path,
    reference: JournalEntryRef,
    *,
    operation: OperationIdentity,
) -> JournalEntry: ...

def load_bundle(
    authorization_root: Path,
    reference: JournalEntryRef,
    *,
    operation: OperationIdentity,
) -> LoadedAuthorizationBundle: ...

def walk_back(
    entry_root: Path,
    authorization_root: Path,
    head: HeadTuple,
    stop: JournalEntryRef,
    *,
    operation: OperationIdentity,
) -> JournalLineage: ...

def validate_lineage(
    lineage: JournalLineage,
    *,
    operation: OperationIdentity,
) -> ValidatedJournalLineage: ...

def authorize_lineage(
    lineage: ValidatedJournalLineage,
    validator: AuthorizationBundleValidator,
    *,
    operation: OperationIdentity,
) -> AuthorizedJournalLineage: ...

def replay_forward(
    replay_input: JournalReplayInput,
    reducer: JournalReducer,
    paths: NetworkHeadPaths,
    *,
    operation: OperationIdentity,
) -> JournalReplayResult: ...

def build_entry_bytes(
    request: JournalEntryBuildRequest,
    *,
    operation: OperationIdentity,
) -> bytes: ...

# tools.mother.common.checkpoints
def locate_newest_valid(
    entry_root: Path,
    head: HeadTuple,
    *,
    operation: OperationIdentity,
) -> CheckpointSelection: ...

def build_state_closure_manifest(
    state_object_root: Path,
    roots: tuple[ContentHash, ...],
    *,
    operation: OperationIdentity,
) -> StateClosureManifestBuildResult: ...

def build_checkpoint(
    request: CheckpointBuildRequest,
    prior_replay: JournalReplayResult | None,
    *,
    operation: OperationIdentity,
) -> CheckpointBuildResult: ...

def build_checkpoint_entry_bytes(
    request: CheckpointEntryBuildRequest,
    prior_replay: JournalReplayResult | None,
    *,
    operation: OperationIdentity,
) -> CheckpointEntryBuildResult: ...

def validate_checkpoint(
    lineage: AuthorizedJournalLineage,
    checkpoint: CheckpointPayload,
    *,
    operation: OperationIdentity,
) -> CheckpointValidationResult: ...

def state_closure(
    state_object_root: Path,
    checkpoint: CheckpointPayload,
    *,
    operation: OperationIdentity,
) -> StateClosure: ...

def prepare_replay(
    lineage: AuthorizedJournalLineage,
    checkpoint_validation: CheckpointValidationResult,
    closure: StateClosure,
    *,
    operation: OperationIdentity,
) -> JournalReplayInput: ...
```

Path ownership is exact. `journal_root` is `paths.journal_head.parent`.
`metadata.json` and `head.json` are direct children of that root.
`entry_root` is the CORE-005-resolved `journal_root / "entries"` immutable
CORE-012 object-store root. `authorization_root` is the distinct
CORE-005-resolved `journal_root / "authorizations"` immutable CORE-012
object-store root. Neither root MAY equal, contain, or be contained by the
other. `state_object_root` is exactly the CORE-005-resolved
`paths.journal_head.parent / "state-objects"` immutable CORE-012 object-store
root for `operation.network`. It is network-scoped, not derived from
`state_schema`, and MUST be distinct from, and neither contain nor be contained
by, `entry_root` and `authorization_root`. STATE-001 and STATE-002 do not read
or write `temporary/` or `archive/` during normal observation, replay, or
building.

The runtime-checkable protocols are exact:

```python
class JournalReducer(Protocol):
    state_schema: str

    def apply(
        self,
        previous_state: bytes,
        event_type: str,
        event_payload: bytes,
    ) -> bytes: ...

class AuthorizationBundleValidator(Protocol):
    def validate_bundle(
        self,
        reference: JournalEntryRef,
        entry: JournalEntry,
        bundle: LoadedAuthorizationBundle,
        *,
        operation: OperationIdentity,
    ) -> None: ...
```

A reducer is a caller-owned pure, deterministic, side-effect-free contract. It
MUST not mutate its inputs, read files, acquire locks, dispatch calls, or depend
on wall-clock time, process identity, locale, hash seed, or iteration order. It
returns canonical JSON bytes for one top-level object in its declared
`state_schema`. STATE-001 applies each event exactly once. One execution cannot
prove general determinism or absence of hidden side effects; STATE-001 detects
only an exception, malformed or non-canonical output, schema disagreement, input
mutation observable at the boundary, or resulting hash disagreement.

`AuthorizationBundleValidator` is the STATE-001 seam through which
`MOTHER-OFM-AUTH-003.validate_bundle` validates every network-journal member.
`authorize_lineage` invokes it exactly once for every member, in descending
sequence order, and returns `AuthorizedJournalLineage` only after every call
succeeds. STATE-001 does not interpret certificate or authority semantics.

`read_stable_head` accepts only an exact `NetworkHeadPaths`. It derives
`metadata.json` from `paths.journal_head.parent`, uses CORE-011
`stable_read(..., max_attempts=3)` around the entire load, validates the exact
metadata and head envelopes, stably reads the committed-state projection, and
requires metadata, head, projection, and supplied operation network identities
to agree. The CORE-011 outer reread occurs after projection parsing and state
hash verification. A causal `MOTHER_STATE_UNSTABLE_READ` from either pointer is
translated to `MOTHER_STATE_UNSTABLE_HEAD` while preserving the cause,
operation identity, `retry_class="after-reobserve"`, and
`authority_effect="none"`. Every other delegated CORE-011 error is propagated
unchanged.

`load_entry` calls CORE-012 `get_verified` against `entry_root` with
`reference.entry_hash`, decodes only the exact entry wire object, reconstructs
canonical `event_payload` bytes, and requires exact journal ID, sequence,
resulting-state hash, and a non-null network authorization-bundle reference.
`load_bundle` requires that exact bundle hash, calls CORE-012
`get_verified` against `authorization_root`, requires canonical JSON bytes for one top-level object, and
returns those exact bytes without interpreting certificate or authority
semantics. `MOTHER-OFM-AUTH-003` remains the sole semantic validator of the
authorization bundle.

`locate_newest_valid` starts from the exact stable head in `entry_root` and
follows verified predecessor entry objects. The first encountered
`event_type="state-checkpoint"` MUST decode and pass intrinsic checkpoint
validation; an invalid committed checkpoint is not skipped. The method returns
that checkpoint plus every later entry reference in forward sequence order.
Reaching sequence 1 without a valid initial checkpoint or encountering a cycle
before a checkpoint raises `MOTHER_STATE_CHECKPOINT_MISSING`.

A missing immutable object has context-specific ownership. A direct
`load_entry` or `load_bundle` request propagates CORE-012
`MOTHER_STATE_OBJECT_MISSING` unchanged. When `locate_newest_valid` follows a
non-null predecessor hash before finding a checkpoint and CORE-012 reports that
object missing, STATE-002 raises `MOTHER_STATE_CHECKPOINT_MISSING`. When
`walk_back` follows any required predecessor in the selected segment and
CORE-012 reports that object missing, STATE-001 raises
`MOTHER_STATE_INVALID_LINEAGE`. Both translations use `retry_class="never"` and
`authority_effect="none"` and retain the complete causal `MotherError`,
operation identity, durable/evidence references, and allowed next actions.
Every other delegated CORE-012 error is preserved unchanged.

`walk_back` records the exact requested `stop` and loads the exact
head-to-checkpoint segment selected above, including the authorization bundle
named by every network-journal reference. It returns members in descending
sequence order and never follows an object not named by the committed lineage.
`validate_lineage` returns a distinct `ValidatedJournalLineage` only when all of
these are true:

1. the first member equals the supplied `HeadTuple` in journal ID, sequence,
   entry hash, bundle hash, and state hash;
2. every member's reference matches its decoded entry and loaded bundle;
3. sequences decrease by exactly one;
4. every child entry's previous entry, previous bundle, and previous state
   hashes equal the next member's reference;
5. journal ID, network, and operation-network binding remain exact;
6. entry and bundle identities do not repeat;
7. sequence 1 has all predecessor fields null, and every later sequence has all
   three predecessor fields non-null;
8. the final member equals `lineage.stop`, and `lineage.stop` is the exact
   requested checkpoint reference.

`authorize_lineage` is mandatory before committed checkpoint validation or
replay. It invokes the supplied `AuthorizationBundleValidator` for every member
and returns a distinct `AuthorizedJournalLineage` with the same immutable
members only after every bundle is semantically valid for its exact entry.

`build_state_closure_manifest` accepts only canonical roots. It recursively
loads each reachable object from `state_object_root`, decodes the exact
`mother.state.object.v1` envelope, derives every child edge exclusively from the
verified object's `references` field, and visits every derived child. It
constructs one edge for every member, including leaves, verifies the derived
graph through CORE-012, computes the exact manifest bytes and SHA-256, and
returns them without publication. It accepts no caller-supplied edges or member
set.

`build_entry_bytes` copies `operation.operation_id`,
`operation.operation_kind`, and `operation.network` into the entry. For
sequence 1, `previous` MUST be `None`; for every later sequence, `previous` is
required and MUST be exactly sequence minus one. The builder computes
`resulting_state_hash`, validates all NFC and canonical-byte rules, preserves
its inputs, and returns only the exact entry bytes. It does not publish an
object or update a head.

`build_checkpoint` computes the state hash and canonical event payload,
performs the intrinsic and construction-time replay checks defined above,
requires canonical unique roots and superseded-lineage tuples, and preserves its
inputs. `build_checkpoint_entry_bytes` calls `build_checkpoint`, enforces the
prospective entry sequence and predecessor rules, and calls
`STATE-001.build_entry_bytes` with the exact checkpoint payload and state. It
does not call `validate_checkpoint`.

`validate_checkpoint` accepts an existing `AuthorizedJournalLineage`. The
lineage stop member is the containing checkpoint entry and its authorization
bundle has already passed AUTH-003 semantic validation. The method requires the
entry event type and payload to equal the checkpoint bytes, the entry reference
to carry the exact loaded authorization-bundle hash, the entry resulting-state
hash to equal the checkpoint state hash, and the coverage fields to bind the
exact predecessor. For `routine`, the containing entry sequence is coverage
sequence plus one, its previous entry hash equals the coverage hash, and its
previous state hash equals the checkpoint state hash. For
`authoritative-rectification`, sequence and predecessor-entry adjacency are
required but the predecessor state hash need not equal the replacement
checkpoint state. `authoritative=True` only for
`authoritative-rectification`.

`state_closure` loads the exact manifest named by
`checkpoint.state_closure_manifest_hash`. It requires manifest roots to equal
`checkpoint.state_object_refs`, independently reloads every manifest member,
re-derives each edge from the member's exact `references` field, and requires
byte-identical canonical manifest rows. It rejects substituted, omitted, extra,
unreachable, duplicate, or cyclic rows and returns the verified manifest hash,
roots, edges, and members. A parent object that names a child cannot be accepted
as a leaf.

Closure error precedence is exact:

```text
checkpoint.state_closure_manifest_hash or checkpoint roots do not bind the
selected verified manifest
  → MOTHER_STATE_CHECKPOINT_INVALID

the selected manifest or an object-derived edge is malformed, disagrees with
canonical object bytes, omits or adds a member or edge, is unreachable, or
contains a cycle
  → MOTHER_RECOVERY_INVALID_CLOSURE

roots, manifest parents, child tuples, derived members, or build inputs contain
duplicate identities
  → MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER
```

A directly requested missing manifest or state object retains the delegated
CORE-012 `MOTHER_STATE_OBJECT_MISSING`. `build_state_closure_manifest` and
`state_closure` own the two closure-domain translations above after exact
objects have been loaded; checkpoint-to-manifest binding is checked first.

`prepare_replay` is the only seam that constructs `JournalReplayInput` or
`CheckpointReplayProof`. Before reading any proof field it verifies the
module-private seals on `AuthorizedJournalLineage`,
`CheckpointValidationResult`, and `StateClosure`. It requires the authorized
lineage stop to equal the checkpoint-validation reference, requires the
validation checkpoint and closure manifest hash and roots to match exactly, and
copies the verified checkpoint state and closure members into the sealed
`CheckpointReplayProof`. No loose checkpoint state, schema, hash, bundle, or
closure tuple is accepted by `replay_forward`.

`replay_forward` accepts only a sealed `JournalReplayInput` containing the exact
sealed `AuthorizedJournalLineage` and sealed `CheckpointReplayProof` produced by
`prepare_replay`. It verifies all three seals before invoking the reducer,
reading the committed-state projection, or rereading the stable head. It then
rechecks the proof cross-bindings, requires the checkpoint state hash to equal
SHA-256 of the canonical checkpoint state, and requires the reducer schema to
equal the proof state schema. It skips the checkpoint entry, reverses later
members into ascending sequence order, verifies each `previous_state_hash`,
applies the reducer once, and requires each resulting canonical state hash to
equal the entry's `resulting_state_hash`. The final hash MUST equal both the
authorized lineage head state hash and the committed-state projection hash.
Before returning, the method calls `read_stable_head` again and requires the
entire `HeadTuple` to equal the proof-bound head. A changed head discards the
replay result and raises `MOTHER_STATE_UNSTABLE_HEAD`; no state derived from the
superseded head is returned.

The future-object prohibition is enforced at both builders and at checkpoint
decode. The recursively closed post-entry field-name set is:

```text
authorization_bundle_hash
authority_reseal_certificate_acceptance_set_root
authority_reseal_certificate_hash
authority_reseal_proposal_hash
certificate_acceptance_set_root
certificate_hash
completed_certificate_hash
proposal_acceptance_set_root
proposal_hash
successor_certificate_hash
transition_acceptance_set_root
transition_decision_hash
transition_decision_record_hash
```

None of those keys MAY occur at any depth in an entry event payload, checkpoint
state, or checkpoint payload, even with `null` or an already existing hash.
`previous_authorization_bundle_hash` in the entry envelope is not prohibited.
`prepared_intent_hash` and `state_closure_manifest_hash` are explicitly
pre-entry and are allowed only in the checkpoint fields defined above. A new
protocol-visible hash role that is created after the successor entry hash does
not become permitted by choosing a new spelling; the governing functionality
and this closed contract MUST first be amended. Raw hash-shaped strings do not
substitute for typed `ContentHash` wire objects.

STATE-001 and STATE-002 are readers plus pure immutable-byte builders. They do
not acquire the network mutation lock, write files, call CORE-012
`put_immutable`, update `head.json`, update `committed-state.json`, create an
authorization bundle, dispatch external calls, or create evidence. Entry and
checkpoint publication is performed by the owning authority or protocol
functionality through CORE-012 after the builder returns. The only writer of
the committed entry/bundle head remains `MOTHER-OFM-AUTH-004`, and the only
writer of the committed-state projection remains `MOTHER-OFM-STATE-003`.
A checkpoint becomes authoritative only when its containing entry and required
authorization bundle are committed through that normal head path.

Every public boundary translates constructor, decoder, reducer, and semantic
validation failures to the exact STATE-001/002 error contract below. Delegated
CORE-011 and CORE-012 errors retain their original code, module ID, retry class,
authority effect, durable-effect references, evidence references, and allowed
next actions except for the two exact missing-predecessor translations defined
above. Those translations retain the complete causal CORE-012 `MotherError`.
STATE-001 and STATE-002 introduce no new durable-effect kind.

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
| `MOTHER_EVIDENCE_*` | malformed evidence, reference mismatch, redaction or export failure |
| `MOTHER_REPORT_*` | malformed derived report, duplicate command, private material |
| `MOTHER_OPEN_*` | implementation blocked by a parent `contract-open` decision |


### 4.4 Normative exact error contracts

Contract tests and public module implementations MAY assert an exact `MOTHER_*`
code only when that code appears in this table. The retry class and authority
effect are part of the code contract and MUST NOT be changed by adapters.

| Exact code | Owning boundary | Retry class | Authority effect | Required meaning |
|---|---|---|---|---|
| `MOTHER_STATE_HASH_MISMATCH` | `MOTHER-OFM-CORE-002` shared typed envelope | `after-reobserve` | `none` | Recomputed content or state hash differs from the declared hash; no authority was changed by detection. |
| `MOTHER_TRANSPORT_VENDOR_FAILURE` | `MOTHER-OFM-CORE-002` vendor-error wrapper for `MOTHER-OFM-XPORT-002` | `after-reobserve` | `live-state-maybe-changed` | A vendor/transport failure was wrapped without exposing secrets or discarding typed durable/evidence references. |
| `MOTHER_STATE_UNSTABLE_READ` | `MOTHER-OFM-CORE-011.stable_read` | `after-reobserve` | `none` | The bounded read/load/reread sequence did not observe an identical pointer pair. |
| `MOTHER_STATE_UNSTABLE_HEAD` | `MOTHER-OFM-STATE-001.read_stable_head` / `MOTHER-OF-OBS-001` | `after-reobserve` | `none` | The state-facing stable-head operation maps a causal `MOTHER_STATE_UNSTABLE_READ` from CORE-011 to this domain code while preserving the typed cause and classifications. |
| `MOTHER_STATE_MALFORMED_JOURNAL_HEAD` | `MOTHER-OFM-STATE-001.read_stable_head` | `never` | `none` | Journal metadata, head bytes, committed-state projection bytes, versions, field sets, NFC strings, state hash, network binding, or the exact head/projection tuple is malformed or contradictory. |
| `MOTHER_STATE_MALFORMED_JOURNAL_ENTRY` | `MOTHER-OFM-STATE-001.load_entry,build_entry_bytes` | `never` | `none` | Stored entry bytes or a build request violate the exact version, field, type, canonical-byte, predecessor, operation-binding, or NFC contract. |
| `MOTHER_STATE_JOURNAL_REFERENCE_MISMATCH` | `MOTHER-OFM-STATE-001.load_entry,load_bundle,walk_back` | `never` | `none` | A typed entry or bundle reference does not match the verified immutable object, sequence, journal identity, state hash, bundle nullability, or expected committed pair. |
| `MOTHER_STATE_INVALID_LINEAGE` | `MOTHER-OFM-STATE-001.walk_back,validate_lineage,authorize_lineage` | `never` | `none` | The committed chain has a sequence gap, duplicate or cycle, inconsistent journal/network identity, a required predecessor object is missing during selected-segment walking, authorization validation fails, or an entry, bundle, or state link mismatches. A translated missing-object error retains the causal CORE-012 `MotherError`. |
| `MOTHER_STATE_REPLAY_FAILED` | `MOTHER-OFM-STATE-001.replay_forward` and `MOTHER-OFM-STATE-002.prepare_replay` | `never` | `none` | A replay proof is absent, malformed, or cross-bound to different lineage/checkpoint/closure values, or a reducer raises, returns malformed or non-canonical bytes, changes schema, observably mutates an input, or produces a state hash that disagrees with an entry, head, or committed-state projection. General nondeterminism and hidden side effects remain caller-contract violations and are not claimed detectable from one application. |
| `MOTHER_STATE_CHECKPOINT_MISSING` | `MOTHER-OFM-STATE-002.locate_newest_valid` | `never` | `none` | The committed retained lineage reaches sequence 1, a cycle, or a missing predecessor object before one valid checkpoint. A translated missing-object error retains the causal CORE-012 `MotherError`. Normal mutation is blocked pending explicit recovery. |
| `MOTHER_STATE_MALFORMED_CHECKPOINT` | `MOTHER-OFM-STATE-002.locate_newest_valid,build_checkpoint,build_checkpoint_entry_bytes,validate_checkpoint` | `never` | `none` | A checkpoint version, kind, field set, canonical payload, constructor value, tuple member, ordering, coverage shape, closure-manifest reference, or NFC string is uninterpretable. |
| `MOTHER_STATE_CHECKPOINT_INVALID` | `MOTHER-OFM-STATE-002.locate_newest_valid,build_checkpoint,build_checkpoint_entry_bytes,validate_checkpoint,state_closure` | `never` | `none` | An interpretable checkpoint fails construction-time prior-replay binding, prospective entry sequence/predecessor/resulting-state binding, committed-read binding to its existing authorized containing entry, exact predecessor, state schema, state bytes, state hash, closure manifest, authoritative kind, or superseded lineage. |
| `MOTHER_STATE_FUTURE_OBJECT_REFERENCE` | `MOTHER-OFM-STATE-001.build_entry_bytes` and `MOTHER-OFM-STATE-002.build_checkpoint,build_checkpoint_entry_bytes,validate_checkpoint` | `never` | `none` | An entry event or checkpoint contains a proposal, certificate, acceptance, decision, authorization-bundle, or other object role created only after the successor entry hash exists. A pre-entry state-closure-manifest hash is permitted. |
| `MOTHER_CONFLICT_DURABLE_TARGET_EXISTS` | `MOTHER-OFM-CORE-011.durable_create` | `after-reobserve` | `none` | Exclusive publication found an already-published target and did not overwrite it. |
| `MOTHER_STATE_DURABLE_TARGET_MISSING` | `MOTHER-OFM-CORE-011.stable_read` | `after-reobserve` | `none` | The required durable pointer or target is absent before any authority effect. |
| `MOTHER_STATE_DURABLE_READ_FAILED` | `MOTHER-OFM-CORE-011` and `MOTHER-OFM-CORE-012` | `after-reobserve` | `none` | A host-filesystem read failed before a new authority effect; the typed cause is retained. |
| `MOTHER_STATE_TARGET_LOCK_CLEANUP_FAILED` | `MOTHER-OFM-CORE-011` and `MOTHER-OFM-CORE-012` | `after-reobserve` | `none` | No publication occurred, or the call was read-only, but target-lock handle cleanup could not be confirmed. The caller reobserves the target and lock state before retrying. |
| `MOTHER_STATE_DURABLE_WRITE_FAILED` | `MOTHER-OFM-CORE-011` and `MOTHER-OFM-CORE-012` | `same-request` | `none` | Temporary creation, complete write, file flush, reread verification, or publication failed before the target became authoritative. |
| `MOTHER_STATE_DURABILITY_UNCONFIRMED` | `MOTHER-OFM-CORE-011` and `MOTHER-OFM-CORE-012` | `after-reobserve` | `local-pointer-determined` | A file, pointer, object, or directory entry exists after publication, but required parent-directory durability was not confirmed. The error carries at least one typed `DurableEffectRef`; reconciliation verifies the bytes and flushes the required directory before reporting success. |
| `MOTHER_STATE_POSTPUBLICATION_CLEANUP_FAILED` | `MOTHER-OFM-CORE-011` and `MOTHER-OFM-CORE-012` | `after-reobserve` | `local-pointer-determined` | Directory durability is confirmed, but lock-handle closure or another post-publication cleanup step is unconfirmed. The durable authority result remains controlling and the error carries its typed `DurableEffectRef`. Failure of an explicit unlock MAY be ignored only when handle closure succeeds, because closure releases the operating-system lock. |
| `MOTHER_INPUT_UNSAFE_PATH` | `MOTHER-OFM-CORE-011` and `MOTHER-OFM-CORE-012` | `never` | `none` | A root, target, object path, existing prefix, or required directory position is a symlink, escapes containment, is occupied by a non-directory, or is otherwise unsafe for durable authority. |
| `MOTHER_STATE_OBJECT_MISSING` | `MOTHER-OFM-CORE-012.get_verified` | `after-reobserve` | `none` | The requested content-addressed object is absent. |
| `MOTHER_STATE_OBJECT_CORRUPT` | `MOTHER-OFM-CORE-012.put_immutable,get_verified` | `never` | `none` | Bytes at a content-addressed path do not hash to that path or conflict with the proposed exact bytes. |
| `MOTHER_RECOVERY_INVALID_CLOSURE` | `MOTHER-OFM-CORE-012.verify_closure,copy_verified_closure` and `MOTHER-OFM-STATE-002.build_state_closure_manifest,state_closure` | `never` | `none` | A selected closure manifest or object-derived graph is malformed, corrupt, cyclic where prohibited, substituted, incomplete, disagrees with canonical object references, or contains unreachable rows. Checkpoint-to-manifest hash or root binding mismatches use `MOTHER_STATE_CHECKPOINT_INVALID` first; directly missing immutable objects retain `MOTHER_STATE_OBJECT_MISSING`. |
| `MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER` | `MOTHER-OFM-CORE-012.verify_closure,copy_verified_closure` and `MOTHER-OFM-STATE-002.build_state_closure_manifest,state_closure` | `never` | `none` | Closure-build roots, manifest parents, child-reference lists, derived members, or verified roots contain duplicate identities. |
| `MOTHER_INPUT_OVERLAPPING_STORAGE_ROOTS` | `MOTHER-OFM-CORE-008.export_manifest`, `MOTHER-OFM-CORE-012.copy_verified_closure`, and `MOTHER-OFM-STATE-001.walk_back` | `never` | `none` | Required distinct object-store trees are equal or one contains the other; no source read or destination write begins. |
| `MOTHER_SCHEMA_UNKNOWN_VERSION` | `MOTHER-OFM-CORE-006`, `MOTHER-OFM-CORE-007`, and `MOTHER-OFM-CORE-010` | `never` | `none` | Canonical bytes or typed inputs declare an unknown schema, capability, or compatibility contract version; no nearby version substitution is allowed. |
| `MOTHER_SCHEMA_MALFORMED_BYTES` | `MOTHER-OFM-CORE-006`, `MOTHER-OFM-CORE-007`, and `MOTHER-OFM-CORE-010` | `never` | `none` | Canonical bytes cannot be decoded into the exact required typed value. |
| `MOTHER_SCHEMA_MALFORMED_OBJECT` | `MOTHER-OFM-CORE-006`, `MOTHER-OFM-CORE-007`, and `MOTHER-OFM-CORE-010` | `never` | `none` | A decoded schema, capability, compatibility report, or requirement object is structurally invalid or contains a value outside the documented closed set. |
| `MOTHER_SCHEMA_MISSING_DEFINITION` | `MOTHER-OFM-CORE-006.load_schema` | `never` | `none` | The requested exact schema ID and version are absent from the supplied typed catalog; no nearby schema substitution is allowed. |
| `MOTHER_SCHEMA_AMBIGUOUS_DECLARATION` | `MOTHER-OFM-CORE-006`, `MOTHER-OFM-CORE-007`, and `MOTHER-OFM-CORE-010` | `never` | `none` | A typed catalog, capability set, report, or frozen requirement set contains duplicate, conflicting, or ambiguous declarations. |
| `MOTHER_SCHEMA_DUPLICATE_REQUIREMENT` | `MOTHER-OFM-CORE-007.require_capabilities` and `MOTHER-OFM-CORE-010.freeze_contract_versions` | `never` | `none` | Frozen schema-flow, capability, or contract-version requirements contain duplicate identities and therefore deterministic invalid input. |
| `MOTHER_EVIDENCE_MALFORMED_DOCUMENT` | `MOTHER-OFM-CORE-008.store_evidence,load_evidence,redact_copy` | `never` | `none` | An evidence document or stored envelope is not the exact supported canonical object, version, field set, or primitive shape. |
| `MOTHER_EVIDENCE_MALFORMED_MANIFEST` | `MOTHER-OFM-CORE-008.load_export_result` | `never` | `none` | A stored manifest document or payload has the wrong metadata, version, canonical field set, entry shape, ordering, or source identity. |
| `MOTHER_EVIDENCE_REFERENCE_MISMATCH` | `MOTHER-OFM-CORE-008.load_evidence,export_manifest,load_export_result` | `never` | `none` | A typed evidence reference does not exactly match the verified stored document metadata, exact source object, or manifest metadata. |
| `MOTHER_EVIDENCE_REDACTION_FAILED` | `MOTHER-OFM-CORE-008.redact_copy` | `never` | `none` | A redaction policy version, pointer, escape, array index, target, normalized duplicate, overlap, or policy ID is invalid; no partial redacted result is returned. |
| `MOTHER_EVIDENCE_PRIVATE_MATERIAL` | `MOTHER-OFM-CORE-008.export_manifest,load_export_result` | `never` | `none` | A derived export has policy `none` or retains a closed private-material key whose value is not exactly `[REDACTED]`. |
| `MOTHER_EVIDENCE_DUPLICATE_EXPORT` | `MOTHER-OFM-CORE-008.export_manifest,load_export_result` | `never` | `none` | Export requests or manifest entries repeat a complete source reference. Multiple sources producing one identical redacted export are permitted. |
| `MOTHER_REPORT_MALFORMED_MODEL` | `MOTHER-OFM-CORE-009` | `never` | `none` | A report model has an unknown version, classification, format, filename binding, operation binding, or structurally invalid typed value. |
| `MOTHER_REPORT_DUPLICATE_COMMAND` | `MOTHER-OFM-CORE-009.build_allowed_commands_report` | `never` | `none` | An allowed-command input repeats a command or supplies conflicting reasons for one command. |
| `MOTHER_REPORT_PRIVATE_MATERIAL` | `MOTHER-OFM-CORE-009` | `never` | `none` | A derived report field contains secret-shaped or private material; no report file is published. |


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
| `MOTHER-OFM-APP-019` | `upgrade_hub.py` | `prep`, `do`, `finalize`, `rollback`; immutable signed Hub release rollout through ordinary D026 |

### 5.2 Core modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-CORE-001` | `common/models.py` | Immutable models from section 4 plus schema-versioned serializers | `pure`; rejects unknown enum/schema values |
| `MOTHER-OFM-CORE-002` | `common/errors.py` | `MotherError`, `wrap_vendor_error`, exit-code mapping | `pure`; never includes secret-bearing values |
| `MOTHER-OFM-CORE-003` | `common/canonical.py` | `canonical_json(value) -> bytes`, `canonical_yaml(value) -> bytes` | `pure`; deterministic UTF-8, normalized keys, no floats or ambiguous scalars in hashed objects |
| `MOTHER-OFM-CORE-004` | `common/hashing.py` | `sha256(bytes)`, `hash_file`, `ordered_root`, `set_root` | `pure` except file read; validates algorithm and canonical member ordering |
| `MOTHER-OFM-CORE-005` | `common/paths.py` | Resolve canonical Mother roots and validate contained paths | `pure`; rejects traversal, symlink escape, wrong network, and wrong generation |
| `MOTHER-OFM-CORE-006` | `common/schemas.py` | `decode_schema_catalog`, `load_schema`, `validate_object`, `validate_schema_transition` | `pure reader`; unknown versions and uninterpretable input raise exact `MOTHER_SCHEMA_*`; known invalid objects or undeclared transitions return typed negative decisions |
| `MOTHER-OFM-CORE-007` | `common/capabilities.py` | `read_capabilities`, `require_capabilities`, `freeze_capability_set` | `pure reader`; malformed or ambiguous input raises exact `MOTHER_SCHEMA_*`; reads and freezes exact capability sets; absent required capabilities return typed negative decisions |
| `MOTHER-OFM-CORE-008` | `common/evidence.py` | exact section 4.1.2 signatures for `store_evidence`, `load_evidence`, `redact_copy`, `export_manifest`, `load_export_result` | immutable local writes only through CORE-012; exact source-object provenance, restart-safe manifest recovery, deterministic redaction, and secret-free content-addressed export |
| `MOTHER-OFM-CORE-009` | `common/reporting.py` | exact section 4.1.2 signatures for `build_evidence_report`, `build_allowed_commands_report`, `render_json`, `render_text`, `render_allowed_commands` | `derived-writer`; deterministic CORE-011 publication beneath one operation report root; rendering never changes authority |
| `MOTHER-OFM-CORE-010` | `common/compatibility.py` | `decode_compatibility_report`, `check_peer_compatibility`, `freeze_contract_versions` | `pure reader`; malformed or ambiguous values raise exact `MOTHER_SCHEMA_*`; ordinary incompatibility returns typed blockers, not durable evidence |
| `MOTHER-OFM-CORE-011` | `common/atomic_files.py` | `stable_read(..., operation: OperationIdentity)`, `durable_create(..., operation: OperationIdentity)`, `durable_replace(..., operation: OperationIdentity)`, `atomic_pointer_cas(..., operation: OperationIdentity)`, `synchronized_target(..., operation: OperationIdentity)` | local durable I/O; durable directory ancestry, temp-write, file fsync, byte reread, rename/replace, directory fsync; CAS mismatch never overwrites; every host-filesystem failure is translated to the exact typed contract; CORE-012 synchronizes only through the public target seam |
| `MOTHER-OFM-CORE-012` | `common/object_store.py` | `put_immutable(..., operation: OperationIdentity)`, `get_verified(..., operation: OperationIdentity)`, `copy_verified_closure(..., operation: OperationIdentity)`, `verify_closure(..., operation: OperationIdentity)` | immutable local I/O; publication and reads synchronize on the CORE-011 target lock; existing hash with different bytes is fatal corruption |
| `MOTHER-OFM-CORE-013` | `common/faultpoints.py` | `hit(name, context)` and production no-op/test interruption implementations | `pure` in production; cannot mutate state, suppress errors, or select an alternate algorithm |

#### 5.2.1 Wave 1C exact public API contracts

The following signatures are the Wave 1C public surface. All returned models
are imported from `tools.mother.common.models`. The `operation` argument is
keyword-only and every raised `MotherError` MUST preserve
`operation.operation_id`, the owning module ID, the documented retry class, and
`authority_effect="none"`.

```python
# MOTHER-OFM-CORE-006 common/schemas.py
decode_schema_catalog(payload: bytes, *, operation: OperationIdentity) -> SchemaCatalog
load_schema(
    catalog: SchemaCatalog,
    schema_id: str,
    schema_version: str,
    *,
    operation: OperationIdentity,
) -> SchemaDefinition
validate_object(
    value: bytes,
    schema: SchemaDefinition,
    *,
    operation: OperationIdentity,
) -> SchemaValidationResult
validate_schema_transition(
    source: SchemaDefinition,
    destination: SchemaDefinition,
    requirement: SchemaFlowRequirement,
    *,
    operation: OperationIdentity,
) -> SchemaTransitionDecision

# MOTHER-OFM-CORE-007 common/capabilities.py
read_capabilities(
    payload: bytes,
    *,
    operation: OperationIdentity,
) -> FrozenCapabilitySet
freeze_capability_set(
    capabilities: CapabilitySet,
    *,
    operation: OperationIdentity,
) -> FrozenCapabilitySet
require_capabilities(
    capabilities: FrozenCapabilitySet,
    requirements: CompatibilityRequirementSet | FrozenCompatibilityContract,
    *,
    operation: OperationIdentity,
) -> CapabilityDecision

# MOTHER-OFM-CORE-010 common/compatibility.py
decode_compatibility_report(
    payload: bytes,
    capabilities: FrozenCapabilitySet,
    *,
    operation: OperationIdentity,
) -> CompatibilityReport
freeze_contract_versions(
    requirements: CompatibilityRequirementSet,
    *,
    operation: OperationIdentity,
) -> FrozenCompatibilityContract
check_peer_compatibility(
    local: CompatibilityReport,
    peer: CompatibilityReport,
    requirements: FrozenCompatibilityContract,
    *,
    operation: OperationIdentity,
) -> CompatibilityDecision
```

CORE-006 follows the decoder precedence in section 4.1.1.
`load_schema` performs exact `schema_id` plus `schema_version` lookup and raises
`MOTHER_SCHEMA_MISSING_DEFINITION` when absent. `validate_object` returns the
exact ordered violation strings from section 4.1.1 for a known schema and an
interpretable object. `validate_schema_transition` first requires
`requirement.schema_id` and `requirement.schema_version` to equal the
destination definition exactly. A mismatch returns exactly one
`schema-transition-requirement-mismatch` blocker. When they match, the method
returns exactly one `schema-transition-undeclared` blocker if the destination
`SchemaVersionRef` is absent from `source.allowed_destinations`; otherwise it
returns a positive decision with no blockers.

CORE-007 follows the decoder precedence in section 4.1.1.
`read_capabilities` returns a canonical `FrozenCapabilitySet`.
`freeze_capability_set` does not mutate its input and sorts capability IDs.
Both reject duplicate IDs with `MOTHER_SCHEMA_AMBIGUOUS_DECLARATION`.
`require_capabilities` rejects duplicate capability-requirement identities with
`MOTHER_SCHEMA_DUPLICATE_REQUIREMENT`; otherwise it returns the exact ordered
`required-capability-absent` blockers from section 4.1.1. Optional
requirements do not block.

CORE-010 follows the decoder precedence in section 4.1.1.
`decode_compatibility_report` raises `MOTHER_SCHEMA_MALFORMED_OBJECT` when the
separately supplied capability participant differs from the report
participant. `freeze_contract_versions` requires
`format_version="compatibility-requirements.v1"`, rejects duplicate
contract-version, schema-flow, or capability-requirement identities with
`MOTHER_SCHEMA_DUPLICATE_REQUIREMENT`, and returns
`format_version="frozen-compatibility-contract.v1"` with every tuple in the
canonical order defined in section 4.1.1. `check_peer_compatibility` requires
local and peer report versions `compatibility-report.v1`, the frozen format
version `frozen-compatibility-contract.v1`, exact participant roles, and
unambiguous report contents. Uninterpretable typed values raise the exact
`MOTHER_SCHEMA_*` error. Ordinary changed versions, missing schemas, or missing
required capabilities return the exact ordered blockers from section 4.1.1.


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
| `MOTHER-OFM-STATE-001` | `common/journal.py` | exact section 4.1.3 signatures for `read_stable_head`, `load_entry`, `load_bundle`, `walk_back`, `validate_lineage`, `authorize_lineage`, `replay_forward`, `build_entry_bytes` | `reader` plus pure immutable entry construction; stable-head, authorization-proof, and replay checks are fail-closed; never publishes an object or updates a head pointer |
| `MOTHER-OFM-STATE-002` | `common/checkpoints.py` | exact section 4.1.3 signatures for `locate_newest_valid`, `build_state_closure_manifest`, `build_checkpoint`, `build_checkpoint_entry_bytes`, `validate_checkpoint`, `state_closure`, `prepare_replay` | `reader` plus pure checkpoint, closure-manifest, and entry-byte construction; validates closure, coverage, committed checkpoint binding, and replay proof; future-object hashes prohibited |
| `MOTHER-OFM-STATE-003` | `common/projections.py` | `render_generation`, `compare_generation`, `build_manifest`, `publish_generation` | `derived-writer`; publication uses one flushed pointer CAS |
| `MOTHER-OFM-STATE-004` | `common/private_state.py` | `read_private_state`, `resolve_validator_ref`, `build_recovery_closure`, `install_verified_private_state` | secret-bearing reader/writer; strict permissions, no general serialization, no plaintext evidence |
| `MOTHER-OFM-STATE-005` | `common/generations.py` | `create_staging`, `seal_generation`, `discard_unpublished`, `switch_active`, `reconcile_active` | local generation writer; active pointer is the commit determinant |

### 5.6 Invocation and transport modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-XPORT-001` | `common/endpoints.py` | `resolve_private_endpoint`, `authorize_target` | `pure` plus inventory read; rejects public, unowned, unapproved method/path/host tuples |
| `MOTHER-OFM-XPORT-002` | `common/call_runner.py` | `dispatch`, `query_status`, `fetch_result` | transport adapter only; never claims target success without durable request evidence |
| `MOTHER-OFM-XPORT-003` | `common/request_journal.py` | `get_or_create_request`, `record_observation`, `resolve_state`, `classify_failure` | `ledger-writer`; same request/body is idempotent and request/body mismatch is fatal; `resolve_state` idempotently persists the state derived from durable observations, permits same-state retries, and rejects conflicting terminal resolution |

### 5.7 Authority and finalization modules

| Module ID | Path | Public API | Effect and failure contract |
|---|---|---|---|
| `MOTHER-OFM-AUTH-001` | `common/successor_reservations.py` | `acquire_full_set`, `resume`, `collect_receipts`, `cancel_full_set`, `release` | replicated reservation writer; exact authority generation and entry hash, all-or-fail current set |
| `MOTHER-OFM-AUTH-002` | `common/certificates.py` | `build_successor_certificate`, `validate_successor_certificate`, `build_ack_certificate`, `validate_ack_certificate`, `validate_acceptances` | `pure` plus immutable object write; full exact set only |
| `MOTHER-OFM-AUTH-003` | `common/authorization.py` | `build_bundle`, `validate_bundle`, `derive_certificate_refs` | replicated-authority object construction; bundle binds existing entry and post-entry evidence only |
| `MOTHER-OFM-AUTH-004` | `common/head_commit.py` | `commit_entry_bundle_pair`, `read_commit_outcome` | sole network-head pointer writer; immutable entry and bundle fsync precede atomic pair commit |
| `MOTHER-OFM-AUTH-005` | `common/replication.py` | `replicate_closure`, `verify_replica`, `resync_exact_head`, `collect_acknowledgements` | distributed authority transport; exact closure and replayed state verification |
| `MOTHER-OFM-AUTH-006` | `common/finalization.py` | `prepare_intent`, `validate_preparatory_progress_transition`, `validate_authoritative_delta`, `build_finalization_entry`, `apply_typed_delta`, `verify_terminal_membership`, `complete_release` | finalization protocol; closed operation-kind-specific deltas change only declared dimensions; commit and replication-pending states remain distinct |
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
| `MOTHER-OFM-SVC-001` | `common/services.py` | `resolve_service`, `capture_service_prestate`, `create_or_repair`, `establish_private_candidate`, `detach_or_remove`, `verify_policy`, `restore`, `enforce_standby`, `observe_release`, `stage_release_artifacts`, `drain_and_apply_prepared_release`, `drain_for_restore`, `verify_release`, `restore_release`, `restore_eligibility` | sole prepared Coolify/service-effect adapter over `MOTHER-OFM-OBS-002`; exact artifact, request, prestate, and frame inputs required |
| `MOTHER-OFM-SVC-002` | `common/hub_release.py` | `validate_descriptor_payload`, `validate_detached_signature`, `validate_signer_policy`, `build_prepared_artifact_stage_request`, `build_prepared_participant_request`, `observe_participant_map`, `validate_legacy_baseline`, `verify_artifact_closures`, `evaluate_compatibility`, `plan_rollout`, `derive_rollout_convergence_and_delta`, `verify_unchanged_dimensions` | `pure`; detached descriptor/signature validation, baseline, compatibility, request construction, rollout policy, and deterministic authoritative-release-state calculation only; performs no deployment or external effect |
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
| `MOTHER-OFM-APP-001` through `MOTHER-OFM-APP-019` | `orchestrator` |
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
| `MOTHER-OFM-SVC-002` | `pure` |
| `MOTHER-OFM-REC-001` | `local-authority-writer` |
| `MOTHER-OFM-REC-002`, `MOTHER-OFM-REC-003` | `replicated-authority-writer` |
| `MOTHER-OFM-MAINT-001`, `MOTHER-OFM-MAINT-002` | `replicated-authority-writer`, disabled while `contract-open` |
| `MOTHER-OFM-MAINT-003` | `derived-writer` |

## 6. Module ownership and concurrency

### 6.1 Exclusive writers

| Resource | Exclusive writer |
|---|---|
| Network journal entry/bundle head pair | `MOTHER-OFM-AUTH-004` |
| Immutable journal entries | `MOTHER-OFM-STATE-001` owns canonical bytes; only the traced authority/protocol caller publishes them through CORE-012 |
| Checkpoints | `MOTHER-OFM-STATE-002` owns the checkpoint payload; its containing entry is built by STATE-001 and published only by the traced authority/protocol caller through CORE-012 |
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
| `MOTHER-OF-OBS-001` | `MOTHER-OFM-CORE-005.resolve_network_head_paths` → `MOTHER-OFM-CORE-011.stable_read` → `MOTHER-OFM-STATE-001.read_stable_head` | Canonically contained head paths and one internally consistent committed `HeadTuple`, or `MOTHER_STATE_UNSTABLE_HEAD`; STATE-001 owns parsing, projection comparison, and the complete bounded stable-read semantics |
| `MOTHER-OF-OBS-002` | `MOTHER-OFM-STATE-001.load_bundle` → `MOTHER-OFM-AUTH-003.validate_bundle` | Bundle bytes and validated binding to the exact entry |
| `MOTHER-OF-OBS-003` | `MOTHER-OFM-STATE-002.locate_newest_valid` → `MOTHER-OFM-STATE-001.load_entry,load_bundle,walk_back,validate_lineage,authorize_lineage` using `MOTHER-OFM-AUTH-003.validate_bundle` for every lineage member → `MOTHER-OFM-STATE-002.validate_checkpoint,state_closure,prepare_replay` → `MOTHER-OFM-STATE-001.replay_forward` | Stable-head-bound replay from the newest valid checkpoint, with every network authorization bundle semantically validated and represented by `AuthorizedJournalLineage`, committed-read checkpoint and derived closure proof bound into `JournalReplayInput`, and exact final state hash |
| `MOTHER-OF-OBS-004` | proof-bearing `JournalReplayInput` from `MOTHER-OF-OBS-003` → `MOTHER-OFM-STATE-001.replay_forward` → `MOTHER-OFM-STATE-003.compare_generation` | Per-projection equal/missing/stale/corrupt classification; loose lineage or checkpoint values are not accepted |
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
| `MOTHER-OF-OBS-015` | `MOTHER-OFM-OBS-006.classify` → `MOTHER-OFM-CORE-009.build_allowed_commands_report,render_json,render_text,render_allowed_commands` | Only the supplied legal commands for the exact classification and active operation, rendered in the exact JSON, text, and command-list formats |
| `MOTHER-OF-OBS-016` | `MOTHER-OFM-CORE-006.validate_object` → `MOTHER-OFM-CORE-007.read_capabilities` → `MOTHER-OFM-CORE-007.require_capabilities` → `MOTHER-OFM-CORE-010.decode_compatibility_report` → `MOTHER-OFM-CORE-010.check_peer_compatibility` | Validated compatibility-report bytes, exact frozen capability read, required-capability decision, typed local/peer reports, and peer-compatibility decision with typed blockers only |
| `MOTHER-OF-OBS-017` | `MOTHER-OFM-OBS-007.run_assertion_set,verify_assertion_evidence` | Complete passed/failed/unknown assertion result |
| `MOTHER-OF-OBS-018` | `MOTHER-OFM-CORE-008.store_evidence,load_evidence,redact_copy,export_manifest,load_export_result` → `MOTHER-OFM-CORE-009.build_evidence_report` → `MOTHER-OFM-CORE-009.render_json` or `MOTHER-OFM-CORE-009.render_text` | Verified immutable evidence, exact source-to-export lineage, restart-reconstructed manifest result, and byte-specified derived report with no private material |

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
| `MOTHER-OF-AUTH-020` | `MOTHER-OFM-CORE-012.verify_closure` → `MOTHER-OFM-OBS-007.verify_assertion_evidence` → `MOTHER-OFM-AUTH-006.validate_preparatory_progress_transition` → `MOTHER-OFM-STATE-001.build_entry_bytes` → `MOTHER-OFM-CORE-012.put_immutable` | Exact retry-idempotent preparatory progress entry: active pending action and prepared-operation hash match, artifact evidence satisfies the frozen contract, only null-to-exact-root or exact-root-to-same-root is permitted, all unrelated fields and the authoritative delta remain byte-identical, and no rollback frame is required or consulted |

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

### 7.9 Hub release rollout

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-REL-001` | `MOTHER-OFM-CORE-006.validate_object` → `MOTHER-OFM-CORE-004.sha256` for the signature-free descriptor payload → `MOTHER-OFM-SVC-002.validate_signer_policy,validate_detached_signature` | Immutable descriptor-payload hash and artifact digest set verified by a detached signature envelope against the independently supplied signer policy; mutable tags rejected |
| `MOTHER-OF-REL-002` | `MOTHER-OFM-SVC-001.observe_release` → `MOTHER-OFM-SVC-002.observe_participant_map` → `MOTHER-OFM-CORE-004.set_root` → `MOTHER-OFM-CORE-008.store_evidence` | Complete current participant-release map and canonical configuration frozen |
| `MOTHER-OF-REL-003` | `MOTHER-OFM-SVC-002.validate_legacy_baseline` → `MOTHER-OFM-CORE-008.store_evidence` | Explicit operator-accepted legacy rollback-baseline evidence, later bound by the one prepared operation record, without finalized release-authority initialization |
| `MOTHER-OF-REL-004` | `MOTHER-OFM-CORE-012.copy_verified_closure,verify_closure` → `MOTHER-OFM-SVC-002.build_prepared_artifact_stage_request` → `MOTHER-OFM-XPORT-003.get_or_create_request` → `MOTHER-OFM-XPORT-002.dispatch(target_handler=MOTHER-OFM-SVC-001.stage_release_artifacts)` → `MOTHER-OFM-XPORT-003.record_observation` → `MOTHER-OFM-SVC-002.verify_artifact_closures` → `MOTHER-OFM-CORE-008.store_evidence` | During `do`, after pending-action opening and before any drain, each exact target/rollback staging request is journaled before dispatch to the sole service-effect handler, and canonical availability evidence is produced |
| `MOTHER-OF-REL-005` | `MOTHER-OFM-SVC-002.evaluate_compatibility,verify_unchanged_dimensions` → `MOTHER-OFM-CORE-007.require_capabilities` → `MOTHER-OFM-OBS-007.run_assertion_set` | Old/new compatibility plus unchanged topology, schemas, configuration, identity, secrets, and membership proven |
| `MOTHER-OF-REL-006` | `MOTHER-OFM-SVC-002.plan_rollout` → `MOTHER-OFM-CTL-002.calculate_desired_state,order_functionalities,calculate_scopes,build_rollback_contract` | Deterministic participant order, continuous or explicit outage policy, scopes, and strict reverse restoration |
| `MOTHER-OF-REL-007` | `MOTHER-OFM-SVC-001.capture_service_prestate,observe_release` → `MOTHER-OFM-RB-001.capture_typed,validate_complete` → `MOTHER-OFM-CORE-012.put_immutable` | Exact artifact, service, process, eligibility, and traffic prestate |
| `MOTHER-OF-REL-008` | `MOTHER-OFM-RB-002.checkpoint_before_dispatch` → `MOTHER-OFM-SVC-002.build_prepared_participant_request` → `MOTHER-OFM-XPORT-003.get_or_create_request` → `MOTHER-OFM-XPORT-002.dispatch(target_handler=MOTHER-OFM-SVC-001.drain_and_apply_prepared_release)` → `MOTHER-OFM-XPORT-003.record_observation` | The exact drain/deployment request exists durably before dispatch invokes the sole service-effect handler on the target |
| `MOTHER-OF-REL-009` | `MOTHER-OFM-XPORT-003.resolve_state,classify_failure` → `MOTHER-OFM-SVC-001.observe_release` → `MOTHER-OFM-OBS-007.run_assertion_set` | Unknown deployment outcome resolved from durable request evidence, exact observed digest, and release assertions without alternate artifact selection |
| `MOTHER-OF-REL-010` | `MOTHER-OFM-SVC-001.verify_release,restore_eligibility` → `MOTHER-OFM-OBS-007.run_assertion_set` | One participant matches its exact target digest, configuration, runtime/API, FDB, health, availability, and prepared-eligibility contract |
| `MOTHER-OF-REL-011` | `MOTHER-OFM-RB-001.load_verified` → `MOTHER-OFM-SVC-001.drain_for_restore` → `MOTHER-OFM-SVC-001.restore_release` → `MOTHER-OFM-SVC-001.verify_release` against the exact predecessor or accepted legacy baseline in the verified frame → `MOTHER-OFM-SVC-001.restore_eligibility` → `MOTHER-OFM-RB-004.verify_restored` | Exact captured artifact, configuration, eligibility, and traffic restored; the forward deployment handler is prohibited |
| `MOTHER-OF-REL-012` | fresh full-set `MOTHER-OFM-SVC-001.observe_release` → `MOTHER-OFM-SVC-002.observe_participant_map,verify_unchanged_dimensions,derive_rollout_convergence_and_delta` → `MOTHER-OFM-CORE-004.set_root` → `MOTHER-OFM-OBS-007.run_assertion_set` → `MOTHER-OFM-CORE-008.store_evidence` | One deterministic convergence proof and exact typed Hub release delta derived solely from fresh full-set observations and the frozen prepared contract; repeated execution MUST be byte-identical |

`MOTHER-OFM-SVC-002` is pure and MUST NOT call Coolify, a registry, a remote
host, or a service. `MOTHER-OFM-SVC-001` remains the exclusive owner of Hub
service observation, artifact staging, drain, deployment, verification, and
restoration effects.

### 7.10 Local adoption

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-SYNC-001` | `MOTHER-OFM-CTL-004.acquire_scope` with local-adoption exclusivity → `MOTHER-OFM-CTL-003.create_prepared` | Owned local-adoption scope retained through terminal state |
| `MOTHER-OF-SYNC-002` | `MOTHER-OFM-STATE-005.reconcile_active` → `MOTHER-OFM-OBS-005.freeze_report_set` → `MOTHER-OFM-REC-001.pin_candidate` | Old local pointer/head and unanimous remote candidate pinned |
| `MOTHER-OF-SYNC-003` | `MOTHER-OFM-STATE-005.create_staging` → `MOTHER-OFM-REC-001.download_to_staging` → `MOTHER-OFM-CORE-012.verify_closure` | Complete immutable candidate closure in unpublished staging |
| `MOTHER-OF-SYNC-004` | `MOTHER-OFM-REC-001.verify_staging` supplies loaded lineage, checkpoint, and state-object roots → `MOTHER-OFM-STATE-001.validate_lineage,authorize_lineage` using `MOTHER-OFM-AUTH-003.validate_bundle` for every lineage member → `MOTHER-OFM-STATE-002.validate_checkpoint,state_closure,prepare_replay` → `MOTHER-OFM-STATE-001.replay_forward` → `MOTHER-OFM-STATE-003.compare_generation` → `MOTHER-OFM-STATE-004.read_private_state` | Candidate lineage, objects, private state, pending actions, and projections verify from one module-sealed replay input; REC-001 does not synthesize proof values |
| `MOTHER-OF-SYNC-005` | `MOTHER-OFM-REC-001.prepare_activation` → `MOTHER-OFM-CORE-011.durable_create` | Activation-prepared evidence outside the swappable generation |
| `MOTHER-OF-SYNC-006` | `MOTHER-OFM-REC-001.switch_pointer` → `MOTHER-OFM-STATE-005.switch_active` | One flushed CAS from pinned old generation to verified candidate |
| `MOTHER-OF-SYNC-007` | `MOTHER-OFM-STATE-005.reconcile_active` → `MOTHER-OFM-REC-001.reconcile` → `MOTHER-OFM-CTL-006.reconcile_from_durable_effect` | Pointer-determined committed or pre-commit state after interruption |
| `MOTHER-OF-SYNC-008` | `MOTHER-OFM-REC-001.discard` → `MOTHER-OFM-STATE-005.discard_unpublished` | Staging discarded while old active pointer remains unchanged |

### 7.11 Lost-local-state recovery

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-REC-001` | `MOTHER-OFM-REC-002.load_descriptor` → `MOTHER-OFM-CORE-006.validate_object` → `MOTHER-OFM-MEM-001.calculate_sets` | Valid descriptor and exact expected replica set |
| `MOTHER-OF-REC-002` | `MOTHER-OFM-OBS-005.collect_reports,freeze_report_set` → `MOTHER-OFM-REC-002.prove_unanimous_candidate` | Unanimous lineage, state, pending-action, private-material, and closure proof |
| `MOTHER-OF-REC-003` | `MOTHER-OFM-REC-002.fetch_objects` → `MOTHER-OFM-CORE-012.copy_verified_closure,verify_closure` | Every required recovery object downloaded and hash-verified |
| `MOTHER-OF-REC-004` | `MOTHER-OFM-STATE-005.create_staging` → `MOTHER-OFM-REC-002.restore_state_root` → `MOTHER-OFM-STATE-004.install_verified_private_state` | Complete recovered local Mother root in immutable generation |
| `MOTHER-OF-REC-005` | `MOTHER-OFM-REC-002.replay_and_verify` supplies loaded lineage, checkpoint, and state-object roots → `MOTHER-OFM-STATE-001.validate_lineage,authorize_lineage` using `MOTHER-OFM-AUTH-003.validate_bundle` for every lineage member → `MOTHER-OFM-STATE-002.validate_checkpoint,state_closure,prepare_replay` → `MOTHER-OFM-STATE-001.replay_forward` → `MOTHER-OFM-STATE-003.render_generation` | Recovered journals replay and projections rebuild from one module-sealed replay input; REC-002 does not synthesize proof values |
| `MOTHER-OF-REC-006` | `MOTHER-OFM-OBS-003.probe_guard,probe_runtime` → `MOTHER-OFM-OBS-007.run_assertion_set` → `MOTHER-OFM-REC-002.prove_unanimous_candidate` comparison | Recovered state matches guards, live assertions, and the frozen recovery candidate |
| `MOTHER-OF-REC-007` | `MOTHER-OFM-REC-002.activate_replacement_identity` → `MOTHER-OFM-STATE-001.build_entry_bytes` → `MOTHER-OFM-AUTH-004.commit_entry_bundle_pair` | Replacement head ID/epoch activation committed at its defined boundary |
| `MOTHER-OF-REC-008` | `MOTHER-OFM-REC-002.replicate_activation` → `MOTHER-OFM-AUTH-005.replicate_closure,collect_acknowledgements` → `MOTHER-OFM-AUTH-002.build_ack_certificate` | Every expected replica acknowledges replacement-head activation |

### 7.12 Authority-restoring reseal

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-RSL-001` | `MOTHER-OFM-OBS-005.collect_reports,freeze_report_set` → `MOTHER-OFM-REC-003.collect_base_reports` → `MOTHER-OFM-CORE-008.store_evidence` | Every current base-authority report plus immutable invalid-head evidence |
| `MOTHER-OF-RSL-002` | `MOTHER-OFM-STATE-001.validate_lineage,authorize_lineage` with `MOTHER-OFM-AUTH-003.validate_bundle` → `MOTHER-OFM-STATE-002.validate_checkpoint,state_closure,prepare_replay` → `MOTHER-OFM-STATE-001.replay_forward` → `MOTHER-OFM-REC-003.prove_common_base` | Common old authority generation and replay-valid selected predecessor proven from one authorized checkpoint/closure replay input |
| `MOTHER-OF-RSL-003` | `MOTHER-OFM-REC-003.calculate_head_sets` → `MOTHER-OFM-CORE-004.set_root` | Observed valid head set and superseded set equal to observed valid heads minus selected predecessor |
| `MOTHER-OF-RSL-004` | `MOTHER-OFM-CTL-003.inspect_active` → `MOTHER-OFM-RB-003.replay` → `MOTHER-OFM-AUTH-001.resume` → `MOTHER-OFM-AUTH-006.verify_terminal_membership` → `MOTHER-OFM-MEM-003.validate_decision` → `MOTHER-OFM-REC-003.classify_obligations` | Each unresolved obligation preserved, carried as remediation-required, or blocks |
| `MOTHER-OF-RSL-005` | `MOTHER-OFM-REC-003.build_intent` → `MOTHER-OFM-CORE-003.canonical_json` → `MOTHER-OFM-CORE-012.put_immutable` | Prepared intent is constructed during `do`, after any actual prospective-readiness root exists, and contains only pre-entry facts plus separate obligation/closure roots |
| `MOTHER-OF-RSL-006` | `MOTHER-OFM-REC-003.build_checkpoint` → `MOTHER-OFM-STATE-002.build_state_closure_manifest` → `MOTHER-OFM-CORE-012.put_immutable` for the pre-entry manifest → `MOTHER-OFM-STATE-002.build_checkpoint,build_checkpoint_entry_bytes` → `MOTHER-OFM-CORE-012.put_immutable` for the entry | Exact successor checkpoint entry binding its derived closure manifest and `prepared_intent_hash`; `build_checkpoint_entry_bytes` invokes the pure `build_checkpoint` seam, and committed-read `validate_checkpoint` is prohibited before entry publication and authorization-bundle commit |
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


### 7.13 Schema migration

These chains remain `contract-open`. Staging and validation APIs are
implementable; authority-changing APIs MUST return
`MOTHER_OPEN_MIGRATION_AUTHORITY`.

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-MIG-001` | `MOTHER-OFM-CORE-006.decode_schema_catalog` → `MOTHER-OFM-CORE-006.load_schema` → `MOTHER-OFM-CORE-006.validate_schema_transition` → `MOTHER-OFM-CORE-007.require_capabilities` | Canonical schema catalog decoded, source/destination schemas loaded, declared transition validated, and migration capability decision produced before any authority-changing migration call |
| `MOTHER-OF-MIG-002` | `MOTHER-OFM-MAINT-001.preserve_source` → `MOTHER-OFM-CORE-012.put_immutable` → `MOTHER-OFM-CORE-008.store_evidence` | Original bytes, hashes, and audit evidence |
| `MOTHER-OF-MIG-003` | `MOTHER-OFM-MAINT-001.resolve_migration,apply_declared` | Deterministic destination bytes from exact source bytes |
| `MOTHER-OF-MIG-004` | `MOTHER-OFM-MAINT-001.validate_graph` → `MOTHER-OFM-CORE-006.validate_object` → `MOTHER-OFM-CORE-012.verify_closure` | Complete migrated graph validates |
| `MOTHER-OF-MIG-005` | `MOTHER-OFM-MAINT-001.build_migrated_object` → for a checkpoint: `MOTHER-OFM-STATE-002.build_state_closure_manifest` → `MOTHER-OFM-CORE-012.put_immutable` for the pre-entry manifest → `MOTHER-OFM-STATE-002.build_checkpoint,build_checkpoint_entry_bytes` → `MOTHER-OFM-CORE-012.put_immutable`; or schema-owned state builder | Content-addressed migrated checkpoint entry or schema-owned state object, with exact source preservation, derived closure provenance, and no committed-read validation before an entry/bundle pair exists |
| `MOTHER-OF-MIG-006` | `MOTHER-OFM-MAINT-001.replicate` → `MOTHER-OFM-AUTH-005.replicate_closure,verify_replica` | Full expected set verifies migrated result |
| `MOTHER-OF-MIG-007` | `MOTHER-OFM-MAINT-001` disabled `commit` | Block until predecessor, certificate, bundle, head, finalization, and rollback authority are defined |
| `MOTHER-OF-MIG-008` | `MOTHER-OFM-MAINT-001.abort` → `MOTHER-OFM-STATE-005.discard_unpublished`; pre-commit restore only | Staging canceled without changing authority |

### 7.14 Identity or secret rotation

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

### 7.15 Projection repair

| Functionality | Ordered module chain | Required result |
|---|---|---|
| `MOTHER-OF-PRJ-001` | `MOTHER-OFM-STATE-001.read_stable_head` → `MOTHER-OFM-MAINT-003.pin_head` | Complete authoritative local head tuple pinned |
| `MOTHER-OF-PRJ-002` | `MOTHER-OFM-MAINT-003.replay_generation` supplies loaded lineage, checkpoint, and state-object roots → `MOTHER-OFM-STATE-001.validate_lineage,authorize_lineage` using `MOTHER-OFM-AUTH-003.validate_bundle` for every lineage member → `MOTHER-OFM-STATE-002.validate_checkpoint,state_closure,prepare_replay` → `MOTHER-OFM-STATE-001.replay_forward` → `MOTHER-OFM-STATE-003.render_generation` | New unpublished projection generation from one module-sealed replay input; MAINT-003 does not synthesize `JournalReplayInput` |
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
| Upgrade Hub | `MOTHER-OP-UPGRADE-HUB` | `MOTHER-OFM-APP-019` | `prep` §20.1; `do` §20.2; `finalize` §20.3; `rollback` §20.4 | `specified` |
| Rollback | active operation identity | `MOTHER-OFM-APP-018` | lifecycle pipeline §21.1 and range semantics §21.2 | `specified` except a parent operation's own open rollback boundary |
| Retry/resume | active operation identity | same entry module as owning operation | `do` retry §22.1; `finalize` retry §22.2 | inherits owning operation |

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
| `evidence/objects/` and `evidence/exports/<operation-id>/objects/` content-addressed evidence objects/manifests | `MOTHER-OFM-CORE-008` through the public `MOTHER-OFM-CORE-012` API | CORE-009 and evidence-export operation read through CORE-008 only |
| content-addressed Hub release descriptors and pinned target/rollback artifact closures | `MOTHER-OFM-CORE-012` | `MOTHER-OFM-SVC-001` stages through object-store API; `MOTHER-OFM-SVC-002` verifies hashes only |
| authority-reseal intent/proposal/certificate evidence | `MOTHER-OFM-REC-003` through declared state/authority writers | Recovery/reporting read |
| `current/` | `MOTHER-OFM-CTL-003` | Diagnose/rollback read; replay-derived only |
| `reports/<operation-id>/` exact filenames in section 4.1.2 | `MOTHER-OFM-CORE-009` through CORE-011 | No authority reader treats reports as source of truth |
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

The object is available only after step 8. `put_immutable`, idempotent
existing-object comparison, and `get_verified` synchronize on the same
cross-process target lock. No caller MAY return object bytes or idempotent
success while another publisher is between steps 7 and 8.

Every newly created directory ancestor is created one level at a time and the
parent of that new directory is flushed before the next level is created.
A retry after `MOTHER_STATE_DURABILITY_UNCONFIRMED` reacquires the same target
lock, verifies the exact published bytes, flushes the required parent
directory, and only then reports success.

Different bytes at an existing hash-derived path are fatal corruption.
Every CORE-011/012 public call receives the real `OperationIdentity`. Host
filesystem exceptions, including absolute-path resolution, temporary-file
descriptor wrapping, complete write, flush, file `fsync`, descriptor closure,
metadata probes, and cleanup, never cross these public boundaries untyped.
A temporary-file failure before publication uses
`MOTHER_STATE_DURABLE_WRITE_FAILED`. A target-lock cleanup failure when no
publication occurred, or after a read-only synchronized call, uses
`MOTHER_STATE_TARGET_LOCK_CLEANUP_FAILED` with `authority_effect=none`.
CORE-011 tracks the exact stage
`prepublication`, `published-unflushed`, or `durable`. A failure before
publication has `authority_effect=none`; a failure after publication but before
confirmed directory durability has `authority_effect=local-pointer-determined`
and carries a typed `DurableEffectRef`; failure after confirmed durability uses
`MOTHER_STATE_POSTPUBLICATION_CLEANUP_FAILED` with the same effect and typed
reference.

After publication, a temporary sibling is non-authoritative. Failure to remove
that sibling MUST NOT prevent the required parent-directory flush or falsely
report that publication did not occur. It MAY be left for bounded cleanup.
Failure of an explicit platform unlock MAY return success only when closing the
lock handle succeeds, because successful handle closure releases the
operating-system lock. CORE-012 MUST synchronize through the public
`MOTHER-OFM-CORE-011.synchronized_target` seam and MUST NOT call private lock
helpers. CORE-012 translates CORE-011 cleanup errors at its public boundary:
post-publication cleanup retains `MOTHER_STATE_POSTPUBLICATION_CLEANUP_FAILED`
but carries an `immutable-object-publication` effect reference owned by
CORE-012; read-only or prepublication lock cleanup uses
`MOTHER_STATE_TARGET_LOCK_CLEANUP_FAILED` with no authority effect.

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
closed operation-kind-specific authoritative delta
  -> finalization-prepared intent and frame-closure evidence
    -> finalization successor entry changing only declared dimensions
      -> successor authority and authorization bundle
        -> atomic local head-pair commit
          -> finalized-replication-pending
            -> exact full-set replication and replay verification
              -> full-set acknowledgement certificate
                -> finalized and terminal release
```

`MOTHER-OFM-AUTH-006.validate_authoritative_delta` MUST reject unknown
dimensions, undeclared state changes, or an operation-kind mismatch before
successor claims begin. Topology operations advance only topology authority.
`upgrade-hub` advances only Hub component-release authority and leaves topology,
topology epoch, schemas, configuration, membership, identities, and secrets
unchanged.

`MOTHER-OFM-AUTH-006.validate_preparatory_progress_transition` is a separate
transition validator. It MUST prove the active pending-action identity and
prepared-operation hash, validate the evidence against the frozen
artifact-availability contract, allow only `null -> exact root` or an
idempotent `exact root -> same exact root`, reject a different retry root, keep
the authoritative delta and every unrelated pending-action field byte-identical,
and require or consult no rollback frame.

Rollback remains available before the atomic finalization head-pair commit and
is closed permanently after it. Replication failure after that commit never
reopens rollback.

### 11.4 Hub release rollout

```text
prepared descriptor payload, detached signature envelope, validated signer
  policy, baseline, closure identities, availability contract, and rollout order
  -> ordinary D026 pending-action-opened entry/bundle commit
    -> request-before-dispatch target and rollback artifact staging
      -> actual artifact-availability evidence root
        -> AUTH-020 preparatory progress entry/bundle commit
          -> per-participant prestate and armed rollback frame
            -> request-before-dispatch exact artifact deployment and verification
              -> promoted frame
                -> AUTH-019 participant-progress entry/bundle commit
                  -> repeat in frozen order
                    -> REL-012 deterministic convergence proof and typed delta
                      -> identical REL-012 finalization calculation and byte equality
                        -> typed Hub release finalization entry/bundle commit
```

`prep` constructs no participant staging result and MUST NOT call
`MOTHER-OFM-SVC-001.stage_release_artifacts`. The pending-action-opened state
binds the expected closure roots and availability contract with a null actual
availability-evidence root. `MOTHER-OF-REL-004` runs only during `do` after the
pending head is authoritative. Its request record MUST exist before
`MOTHER-OFM-XPORT-002.dispatch` invokes
`MOTHER-OFM-SVC-001.stage_release_artifacts`. No drain or deployment is
permitted until the actual evidence root is committed through AUTH-020 without
requiring a promoted rollback frame.

REL-008 follows the same request-before-dispatch rule:
`checkpoint_before_dispatch` precedes pure request construction, durable request
creation, and dispatch; dispatch invokes
`MOTHER-OFM-SVC-001.drain_and_apply_prepared_release` as its target handler.
The finalization successor changes only Hub component-release authority and
retains the descriptor-payload hash, detached signature-envelope hash,
validated signer-policy hash, participant-map root, and release generation.
D028/D029 evidence, mutable image tags, and undeclared topology, schema,
configuration, identity, secret, or membership changes are invalid.

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
| `MOTHER-REQ-027` | `MOTHER-OFM-APP-019`, `MOTHER-OFM-SVC-001`, `MOTHER-OFM-SVC-002`, `MOTHER-OFM-AUTH-001` through `007`, `MOTHER-OFM-RB-001` through `004`, `MOTHER-OFM-XPORT-001` through `003` |

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
9. identity, pure Hub-release policy, service, QBFT, routing, Hub/FDB, and
   governance adapters;
10. read-only operation entry modules;
11. `sync-state`, `recover-head`, and `reseal-state` protocol modules;
12. mutating operation entry modules, including `upgrade-hub` only after service,
    artifact-closure, rollback, D026, and typed-finalization contracts pass;
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

- 27 unique requirement IDs and 30 unique design IDs in `mother.md`;
- 17 unique operation IDs in `mother-o.md`;
- 182 unique non-gap functionality IDs in `mother-o-f.md`;
- 82 unique module IDs in this document;
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
methods
```

Protocol and operation tests additionally record stage, request identity,
durable evidence references, and the ordered functionality/module calls they
observed. Collection fails immediately on unknown identifiers, missing module or method
ancestry, prohibited gap references, or an invalid contract-open mutation test.
`methods` entries use the exact `MOTHER-OFM-<family>-<number>.<callable>`
form and MUST appear in the claimed functionality chain. A method belonging to
an unrelated functionality or to an unclaimed module is a collection error.
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
