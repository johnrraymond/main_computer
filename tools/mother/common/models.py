"""Immutable, schema-versioned values shared by Mother modules.

This module is intentionally dependency-free.  It owns the value types that
cross Mother module boundaries and rejects non-canonical enum/hash values at
construction time.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, fields, is_dataclass
from functools import wraps
from pathlib import Path
from enum import Enum
from types import MappingProxyType
from types import UnionType
from typing import Any, ClassVar, Mapping, TypeVar, get_args, get_origin, get_type_hints
from collections.abc import Mapping as ABCMapping


SCHEMA_VERSION = 1

OPERATION_KINDS = frozenset(
    {
        "MOTHER-OP-DIAGNOSE",
        "MOTHER-OP-PLAN",
        "MOTHER-OP-EVIDENCE-EXPORT",
        "MOTHER-OP-ADD-NODE",
        "MOTHER-OP-REMOVE-NODE",
        "MOTHER-OP-RESTORE-SERVICE",
        "MOTHER-OP-RESEAL-QBFT",
        "MOTHER-OP-RPC-PROPAGATE",
        "MOTHER-OP-SYNC-STATE",
        "MOTHER-OP-RECOVER-HEAD",
        "MOTHER-OP-RESEAL-STATE",
        "MOTHER-OP-REPLICA-ENROLL",
        "MOTHER-OP-REPLICA-RETIRE",
        "MOTHER-OP-SCHEMA-MIGRATION",
        "MOTHER-OP-IDENTITY-ROTATION",
        "MOTHER-OP-REPAIR-PROJECTIONS",
        "MOTHER-OP-UPGRADE-HUB",
    }
)

ROLLBACK_SELECTOR_KINDS = frozenset({"all", "count", "through-layer"})
PARTICIPANT_RESULT_STATES = frozenset(
    {"accepted", "running", "succeeded", "failed", "unknown"}
)


class FrozenMapping(Mapping[str, Any]):
    """Small immutable mapping with value semantics and deterministic equality."""

    __slots__ = ("_data", "_hash")

    def __init__(self, values: Mapping[str, Any] | None = None) -> None:
        if values is not None and not isinstance(values, Mapping):
            raise TypeError("FrozenMapping values must be a mapping")
        data: dict[str, Any] = {}
        for key, value in (values or {}).items():
            if not isinstance(key, str):
                raise TypeError("FrozenMapping keys must be strings")
            data[key] = _freeze(value)
        self._data = MappingProxyType(data)
        self._hash: int | None = None

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"FrozenMapping({dict(self._data)!r})"

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(tuple(sorted((key, _hashable(value)) for key, value in self._data.items())))
        return self._hash


def _hashable(value: Any) -> Any:
    if isinstance(value, FrozenMapping):
        return tuple(sorted((key, _hashable(item)) for key, item in value.items()))
    if isinstance(value, tuple):
        return tuple(_hashable(item) for item in value)
    if is_dataclass(value):
        return tuple((field.name, _hashable(getattr(value, field.name))) for field in fields(value))
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, FrozenMapping):
        return value
    if isinstance(value, Mapping):
        return FrozenMapping(value)
    if isinstance(value, (set, frozenset)):
        raise TypeError("unordered set values are not permitted in Mother models")
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _require_identifier(value: str, field_name: str) -> str:
    _require_text(value, field_name)
    if value in {".", ".."} or any(ch in value for ch in ("/", "\\", "\x00")):
        raise ValueError(f"invalid {field_name}")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if value[0] not in allowed or any(ch not in allowed for ch in value):
        raise ValueError(f"invalid {field_name}")
    return value


def _require_text(value: str, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{field_name} must not be empty")
    return value


def _freeze_fields(instance: Any, names: tuple[str, ...]) -> None:
    for name in names:
        object.__setattr__(instance, name, _freeze(getattr(instance, name)))


@dataclass(frozen=True, slots=True)
class ContentHash:
    algorithm: str
    digest: str
    schema_version: ClassVar[int] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.algorithm != "sha256":
            raise ValueError("only sha256 content hashes are supported")
        if not isinstance(self.digest, str):
            raise TypeError("digest must be a string")
        if len(self.digest) != 64 or self.digest.lower() != self.digest:
            raise ValueError("sha256 digest must be 64 lowercase hexadecimal characters")
        try:
            int(self.digest, 16)
        except ValueError as exc:
            raise ValueError("digest is not lowercase hexadecimal") from exc


@dataclass(frozen=True, slots=True)
class NetworkHeadPaths:
    journal_head: Path
    committed_state: Path
    schema_version: ClassVar[int] = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ProjectionPaths:
    generations_root: Path
    active_pointer: Path
    schema_version: ClassVar[int] = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class PrivateStatePaths:
    root: Path
    identity_file: Path
    metadata_file: Path
    recovery_objects_root: Path
    recovery_manifest: Path
    schema_version: ClassVar[int] = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class GenerationPaths:
    generations_root: Path
    active_pointer: Path
    schema_version: ClassVar[int] = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class PrivateStateBinding:
    private_state_kind: str
    generation: int
    content_hash: ContentHash
    recovery_manifest_hash: ContentHash
    schema_version: ClassVar[int] = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.private_state_kind != "main_computer.mother.private_state.v1":
            raise ValueError("unknown private-state kind")
        if type(self.generation) is not int or self.generation <= 0:
            raise ValueError("generation must be a positive integer")


@dataclass(frozen=True, slots=True)
class HeadTuple:
    journal_identity: str
    sequence: int
    entry_hash: ContentHash
    authorization_bundle_hash: ContentHash
    state_hash: ContentHash
    head_id: str
    head_epoch: int

    def __post_init__(self) -> None:
        _require_text(self.journal_identity, "journal_identity")
        _require_text(self.head_id, "head_id")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("sequence must be a non-negative integer")
        if type(self.head_epoch) is not int or self.head_epoch < 0:
            raise ValueError("head_epoch must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ReplicaSets:
    current: tuple[str, ...] = ()
    prospective: tuple[str, ...] = ()
    transition: tuple[str, ...] = ()
    desired: tuple[str, ...] = ()
    retiring: tuple[str, ...] = ()
    successor_authority: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _freeze_fields(
            self,
            ("current", "prospective", "transition", "desired", "retiring", "successor_authority"),
        )


@dataclass(frozen=True, slots=True)
class AuthorityGeneration:
    predecessor_head: HeadTuple | None
    synthetic_birth_generation: str | None
    current_replicas: tuple[str, ...]
    authority_participants: tuple[str, ...]

    def __post_init__(self) -> None:
        if (self.predecessor_head is None) == (self.synthetic_birth_generation is None):
            raise ValueError(
                "exactly one of predecessor_head or synthetic_birth_generation is required"
            )
        _freeze_fields(self, ("current_replicas", "authority_participants"))




_DURABLE_EFFECT_KINDS = frozenset(
    {
        "local-directory-creation",
        "local-file-publication",
        "local-pointer-publication",
        "immutable-object-publication",
    }
)


@dataclass(frozen=True, slots=True)
class DurableEffectRef:
    """Typed identity for a completed or potentially completed local effect."""

    effect_kind: str
    target: str
    content_hash: ContentHash

    def __post_init__(self) -> None:
        if self.effect_kind not in _DURABLE_EFFECT_KINDS:
            raise ValueError(f"unknown durable effect kind: {self.effect_kind!r}")
        _require_text(self.target, "target")


@dataclass(frozen=True, slots=True)
class OperationIdentity:
    operation_id: str
    request_id: str
    network: str
    operation_kind: str

    def __post_init__(self) -> None:
        _require_text(self.operation_id, "operation_id")
        _require_text(self.request_id, "request_id")
        _require_text(self.network, "network")
        if self.operation_kind not in OPERATION_KINDS:
            raise ValueError(f"unknown Mother operation kind: {self.operation_kind!r}")


@dataclass(frozen=True, slots=True)
class OperationIntent:
    operation_kind: str
    network: str
    explicit_targets: tuple[str, ...] = ()
    mode: str = "default"
    options: Mapping[str, Any] = FrozenMapping()
    reason: str = ""
    client_request_identity: str = ""

    def __post_init__(self) -> None:
        if self.operation_kind not in OPERATION_KINDS:
            raise ValueError(f"unknown Mother operation kind: {self.operation_kind!r}")
        _require_text(self.network, "network")
        _freeze_fields(self, ("explicit_targets", "options"))


@dataclass(frozen=True, slots=True)
class MutationScope:
    type: str
    canonical_resource_identity: str
    authority_generation: str
    owning_operation: str

    def __post_init__(self) -> None:
        for name in (
            "type",
            "canonical_resource_identity",
            "authority_generation",
            "owning_operation",
        ):
            _require_text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class OperationRecord:
    identity: OperationIdentity
    intent: OperationIntent
    frozen_inputs: Mapping[str, Any] = FrozenMapping()
    scopes: tuple[MutationScope, ...] = ()
    state: str = "prepared"
    allowed_commands: tuple[str, ...] = ()
    immutable_evidence_roots: tuple[ContentHash, ...] = ()

    def __post_init__(self) -> None:
        _freeze_fields(
            self,
            ("frozen_inputs", "scopes", "allowed_commands", "immutable_evidence_roots"),
        )


@dataclass(frozen=True, slots=True)
class RollbackSelector:
    selection: str
    count: int | None = None
    through_layer: str | None = None
    operation_id: str | None = None

    def __post_init__(self) -> None:
        if self.selection not in ROLLBACK_SELECTOR_KINDS:
            raise ValueError(f"unknown rollback selector: {self.selection!r}")
        if self.selection == "count":
            if type(self.count) is not int or self.count <= 0:
                raise ValueError("count selection requires a positive count")
        elif self.count is not None:
            raise ValueError("count is valid only for the count selector")
        if self.selection == "through-layer":
            _require_text(self.through_layer or "", "through_layer")
        elif self.through_layer is not None:
            raise ValueError("through_layer is valid only for through-layer selection")


@dataclass(frozen=True, slots=True)
class OperationCommandResult:
    operation_identity: OperationIdentity
    prior_state: str
    current_state: str
    durable_effects: tuple[Any, ...] = ()
    evidence_refs: tuple[Any, ...] = ()
    report_refs: tuple[Any, ...] = ()
    allowed_next_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _freeze_fields(
            self,
            ("durable_effects", "evidence_refs", "report_refs", "allowed_next_actions"),
        )


@dataclass(frozen=True, slots=True)
class SuccessorReservation:
    authority_generation: str
    successor_entry_hash: ContentHash
    participant_set: tuple[str, ...]
    receipts: tuple[Any, ...] = ()
    status: str = "prepared"

    def __post_init__(self) -> None:
        _freeze_fields(self, ("participant_set", "receipts"))


@dataclass(frozen=True, slots=True)
class CertificateRef:
    kind: str
    schema_version: int
    object_hash: ContentHash
    bound_entry_hash: ContentHash

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version <= 0:
            raise ValueError("schema_version must be positive")


@dataclass(frozen=True, slots=True)
class AuthorizationBundleRef:
    bundle_hash: ContentHash
    entry_hash: ContentHash
    certificate_kind: str
    certificate_hash: ContentHash
    membership_roots: tuple[ContentHash, ...] = ()

    def __post_init__(self) -> None:
        _freeze_fields(self, ("membership_roots",))


@dataclass(frozen=True, slots=True)
class RollbackFrame:
    frame_id: str
    operation_id: str
    phase: str
    typed_prestate_ref: Any
    restore_contract: Mapping[str, Any]
    status: str

    def __post_init__(self) -> None:
        _freeze_fields(self, ("typed_prestate_ref", "restore_contract"))


@dataclass(frozen=True, slots=True)
class ParticipantRequest:
    request_id: str
    operation_id: str
    participant: str
    method: str
    path: str
    body_hash: ContentHash | None = None


@dataclass(frozen=True, slots=True)
class ParticipantResult:
    durable_state: str
    result_hash: ContentHash | None = None
    target_rejection: str | None = None
    transport_observation: Mapping[str, Any] = FrozenMapping()

    def __post_init__(self) -> None:
        if self.durable_state not in PARTICIPANT_RESULT_STATES:
            raise ValueError(f"unknown participant result state: {self.durable_state!r}")
        _freeze_fields(self, ("transport_observation",))


@dataclass(frozen=True, slots=True)
class StateGeneration:
    generation_id: str
    immutable_root: ContentHash
    manifest_hash: ContentHash
    active_pointer_predecessor: ContentHash | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.generation_id, "generation_id")


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    object_hash: ContentHash
    schema: str
    redaction_policy: str
    source: str
    observation_time: str


@dataclass(frozen=True, slots=True)
class HubReleaseDescriptorPayload:
    release_id: str
    image_manifest_digest: ContentHash
    platform_image_digests: Mapping[str, ContentHash]
    source_commit: str
    provenance_attestation_hash: ContentHash
    runtime_contract_version: str
    hub_api_version: str
    fdb_schema_version: str
    data_schema_change: bool
    compatible_from_releases: tuple[ContentHash, ...] = ()
    compatible_mixed_release_sets: tuple[Any, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    health_assertion_set_hash: ContentHash | None = None

    def __post_init__(self) -> None:
        _freeze_fields(
            self,
            (
                "platform_image_digests",
                "compatible_from_releases",
                "compatible_mixed_release_sets",
                "required_capabilities",
            ),
        )


@dataclass(frozen=True, slots=True)
class HubReleaseSignatureEnvelope:
    descriptor_payload_hash: ContentHash
    image_manifest_digest: ContentHash
    platform_image_digests: Mapping[str, ContentHash]
    signer_identity: str
    signature_algorithm: str
    signature_bytes: bytes

    def __post_init__(self) -> None:
        _freeze_fields(self, ("platform_image_digests",))
        if not isinstance(self.signature_bytes, bytes) or not self.signature_bytes:
            raise ValueError("signature_bytes must be non-empty bytes")


@dataclass(frozen=True, slots=True)
class HubReleaseAuthorization:
    descriptor_payload_hash: ContentHash
    signature_envelope_hash: ContentHash
    validated_signer_policy_hash: ContentHash


@dataclass(frozen=True, slots=True)
class HubComponentReleaseState:
    descriptor_payload_hash: ContentHash
    signature_envelope_hash: ContentHash
    validated_signer_policy_hash: ContentHash
    participant_release_map_root: ContentHash
    release_generation: int

    def __post_init__(self) -> None:
        if type(self.release_generation) is not int or self.release_generation <= 0:
            raise ValueError("release_generation must be positive")


@dataclass(frozen=True, slots=True)
class AuthoritativeDelta:
    operation_kind: str
    predecessor_dimensions: Mapping[str, Any]
    successor_dimensions: Mapping[str, Any]
    unchanged_dimensions: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.operation_kind not in OPERATION_KINDS:
            raise ValueError(f"unknown Mother operation kind: {self.operation_kind!r}")
        _freeze_fields(
            self,
            ("predecessor_dimensions", "successor_dimensions", "unchanged_dimensions"),
        )



WAVE1C_PARTICIPANTS = frozenset({"local", "peer"})
WAVE1C_BLOCKER_CODES = frozenset(
    {
        "contract-version-set-changed",
        "schema-producer-unsupported",
        "schema-consumer-unsupported",
        "required-capability-absent",
        "schema-transition-requirement-mismatch",
        "schema-transition-undeclared",
    }
)


def _require_participant(value: str, field_name: str) -> None:
    if value not in WAVE1C_PARTICIPANTS:
        raise ValueError(f"{field_name} must be 'local' or 'peer'")


def _require_nonempty_tuple_strings(values: tuple[str, ...], field_name: str) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{field_name} must be a tuple")
    for index, value in enumerate(values):
        _require_text(value, f"{field_name}[{index}]")


@dataclass(frozen=True, slots=True)
class SchemaVersionRef:
    schema_id: str
    schema_version: str

    def __post_init__(self) -> None:
        _require_text(self.schema_id, "schema_id")
        _require_text(self.schema_version, "schema_version")


@dataclass(frozen=True, slots=True)
class SchemaFlowRequirement:
    schema_id: str
    schema_version: str
    producer: str
    consumer: str

    def __post_init__(self) -> None:
        _require_text(self.schema_id, "schema_id")
        _require_text(self.schema_version, "schema_version")
        _require_participant(self.producer, "producer")
        _require_participant(self.consumer, "consumer")
        if self.producer == self.consumer:
            raise ValueError("producer and consumer must be different")


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    capability_id: str
    executor: str
    required: bool

    def __post_init__(self) -> None:
        _require_text(self.capability_id, "capability_id")
        _require_participant(self.executor, "executor")
        if type(self.required) is not bool:
            raise TypeError("required must be a boolean")


@dataclass(frozen=True, slots=True)
class CompatibilityRequirementSet:
    format_version: str
    local_contract_versions: tuple[str, ...]
    peer_contract_versions: tuple[str, ...]
    schema_flows: tuple[SchemaFlowRequirement, ...]
    capability_requirements: tuple[CapabilityRequirement, ...]

    def __post_init__(self) -> None:
        _require_text(self.format_version, "format_version")
        _require_nonempty_tuple_strings(
            self.local_contract_versions, "local_contract_versions"
        )
        _require_nonempty_tuple_strings(
            self.peer_contract_versions, "peer_contract_versions"
        )


@dataclass(frozen=True, slots=True)
class FrozenCompatibilityContract:
    format_version: str
    local_contract_versions: tuple[str, ...]
    peer_contract_versions: tuple[str, ...]
    schema_flows: tuple[SchemaFlowRequirement, ...]
    capability_requirements: tuple[CapabilityRequirement, ...]

    def __post_init__(self) -> None:
        _require_text(self.format_version, "format_version")
        _require_nonempty_tuple_strings(
            self.local_contract_versions, "local_contract_versions"
        )
        _require_nonempty_tuple_strings(
            self.peer_contract_versions, "peer_contract_versions"
        )


@dataclass(frozen=True, slots=True)
class CompatibilityBlocker:
    code: str
    subject_id: str
    participant: str
    detail: str

    def __post_init__(self) -> None:
        if self.code not in WAVE1C_BLOCKER_CODES:
            raise ValueError(f"unknown compatibility blocker code: {self.code!r}")
        _require_text(self.subject_id, "subject_id")
        _require_participant(self.participant, "participant")
        _require_text(self.detail, "detail")


@dataclass(frozen=True, slots=True)
class CompatibilityDecision:
    compatible: bool
    blockers: tuple[CompatibilityBlocker, ...]
    local_contract_versions: tuple[str, ...]
    peer_contract_versions: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.compatible) is not bool:
            raise TypeError("compatible must be a boolean")
        _require_nonempty_tuple_strings(
            self.local_contract_versions, "local_contract_versions"
        )
        _require_nonempty_tuple_strings(
            self.peer_contract_versions, "peer_contract_versions"
        )


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    participant: str
    contract_version: str
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_participant(self.participant, "participant")
        _require_text(self.contract_version, "contract_version")
        _require_nonempty_tuple_strings(self.capabilities, "capabilities")


@dataclass(frozen=True, slots=True)
class FrozenCapabilitySet:
    participant: str
    contract_version: str
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_participant(self.participant, "participant")
        _require_text(self.contract_version, "contract_version")
        _require_nonempty_tuple_strings(self.capabilities, "capabilities")


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    report_version: str
    participant: str
    contract_versions: tuple[str, ...]
    produced_schemas: tuple[SchemaVersionRef, ...]
    consumed_schemas: tuple[SchemaVersionRef, ...]
    capabilities: FrozenCapabilitySet

    def __post_init__(self) -> None:
        _require_text(self.report_version, "report_version")
        _require_participant(self.participant, "participant")
        _require_nonempty_tuple_strings(self.contract_versions, "contract_versions")


@dataclass(frozen=True, slots=True)
class SchemaDefinition:
    schema_id: str
    schema_version: str
    object_kind: str
    required_field_names: tuple[str, ...]
    optional_field_names: tuple[str, ...]
    allowed_destinations: tuple[SchemaVersionRef, ...]

    def __post_init__(self) -> None:
        _require_text(self.schema_id, "schema_id")
        _require_text(self.schema_version, "schema_version")
        _require_text(self.object_kind, "object_kind")
        _require_nonempty_tuple_strings(
            self.required_field_names, "required_field_names"
        )
        _require_nonempty_tuple_strings(
            self.optional_field_names, "optional_field_names"
        )


@dataclass(frozen=True, slots=True)
class SchemaCatalog:
    catalog_id: str
    catalog_version: str
    schemas: tuple[SchemaDefinition, ...]

    def __post_init__(self) -> None:
        _require_text(self.catalog_id, "catalog_id")
        _require_text(self.catalog_version, "catalog_version")


@dataclass(frozen=True, slots=True)
class SchemaValidationResult:
    schema_id: str
    schema_version: str
    valid: bool
    violations: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.schema_id, "schema_id")
        _require_text(self.schema_version, "schema_version")
        if type(self.valid) is not bool:
            raise TypeError("valid must be a boolean")
        _require_nonempty_tuple_strings(self.violations, "violations")


@dataclass(frozen=True, slots=True)
class SchemaTransitionDecision:
    compatible: bool
    blockers: tuple[CompatibilityBlocker, ...]

    def __post_init__(self) -> None:
        if type(self.compatible) is not bool:
            raise TypeError("compatible must be a boolean")


@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    allowed: bool
    blockers: tuple[CompatibilityBlocker, ...]

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise TypeError("allowed must be a boolean")

_MODEL_TYPES = {
    cls.__name__: cls
    for cls in (
        ContentHash,
        NetworkHeadPaths,
        ProjectionPaths,
        PrivateStatePaths,
        GenerationPaths,
        PrivateStateBinding,
        HeadTuple,
        ReplicaSets,
        AuthorityGeneration,
        DurableEffectRef,
        OperationIdentity,
        OperationIntent,
        MutationScope,
        OperationRecord,
        RollbackSelector,
        OperationCommandResult,
        SuccessorReservation,
        CertificateRef,
        AuthorizationBundleRef,
        RollbackFrame,
        ParticipantRequest,
        ParticipantResult,
        StateGeneration,
        EvidenceRef,
        HubReleaseDescriptorPayload,
        HubReleaseSignatureEnvelope,
        HubReleaseAuthorization,
        HubComponentReleaseState,
        AuthoritativeDelta,
        SchemaVersionRef,
        SchemaFlowRequirement,
        CapabilityRequirement,
        CompatibilityRequirementSet,
        FrozenCompatibilityContract,
        CompatibilityBlocker,
        CompatibilityDecision,
        CompatibilityReport,
        SchemaCatalog,
        SchemaDefinition,
        SchemaValidationResult,
        SchemaTransitionDecision,
        CapabilitySet,
        FrozenCapabilitySet,
        CapabilityDecision,
    )
}



class _SerializedModelDict(dict[str, Any]):
    """Ordered in-memory model mapping that preserves a field named schema_version.

    The generic model envelope and a declared model field can share the normative
    label ``schema_version``.  This mapping preserves declaration-order iteration
    for contract inspection while retaining the declared field value separately
    for exact in-memory deserialization.
    """

    __slots__ = ("_ordered_names", "_field_values")

    def __init__(self, field_values: Mapping[str, Any]) -> None:
        base = {"schema_version": SCHEMA_VERSION}
        base.update(
            (name, value)
            for name, value in field_values.items()
            if name != "schema_version"
        )
        super().__init__(base)
        self._ordered_names = ("schema_version", *tuple(field_values))
        self._field_values = dict(field_values)

    def __iter__(self):
        return iter(self._ordered_names)

    def __len__(self) -> int:
        return len(self._ordered_names)

    def declared_field_value(self, name: str) -> Any:
        return self._field_values[name]


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        field_values = {
            field.name: _serialize(getattr(value, field.name))
            for field in fields(value)
        }
        if "schema_version" in field_values:
            return _SerializedModelDict(field_values)
        return {"schema_version": SCHEMA_VERSION, **field_values}
    if isinstance(value, FrozenMapping):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("mapping keys must be strings")
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, bytes):
        return {"encoding": "hex", "value": value.hex()}
    if isinstance(value, Enum):
        return value.value
    return value


def serialize_model(value: Any) -> dict[str, Any]:
    """Return the schema-v1 mapping for a declared Mother model."""

    if type(value).__name__ not in _MODEL_TYPES or not is_dataclass(value):
        raise TypeError("value is not a declared Mother model")
    serialized = _serialize(value)
    assert isinstance(serialized, dict)
    return serialized


T = TypeVar("T")


def _decode_untyped(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_decode_untyped(item) for item in value)
    if isinstance(value, Mapping):
        if set(value) == {"encoding", "value"} and value.get("encoding") == "hex":
            encoded = value.get("value")
            if not isinstance(encoded, str):
                raise TypeError("hex-encoded bytes require a string value")
            try:
                return bytes.fromhex(encoded)
            except ValueError as exc:
                raise ValueError("invalid hex-encoded bytes") from exc
        if any(not isinstance(key, str) for key in value):
            raise TypeError("mapping keys must be strings")
        return FrozenMapping({key: _decode_untyped(item) for key, item in value.items()})
    return value


def _decode(annotation: Any, value: Any) -> Any:
    if annotation is Any:
        return _decode_untyped(value)

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin in (tuple,):
        if not isinstance(value, (list, tuple)):
            raise TypeError("tuple field requires an array")
        item_annotation = args[0] if args else Any
        return tuple(_decode(item_annotation, item) for item in value)

    if origin in (dict, Mapping, ABCMapping) or annotation in (dict, Mapping, ABCMapping, FrozenMapping):
        if not isinstance(value, Mapping):
            raise TypeError("mapping field requires an object")
        key_annotation, item_annotation = args if len(args) == 2 else (str, Any)
        decoded: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("mapping keys must be strings")
            if key_annotation not in (str, Any):
                raise TypeError("Mother mappings support string keys only")
            decoded[key] = _decode(item_annotation, item)
        return FrozenMapping(decoded)

    if origin in (UnionType,) or str(origin) == "typing.Union":
        if value is None and type(None) in args:
            return None
        failures: list[Exception] = []
        for option in args:
            if option is type(None):
                continue
            try:
                return _decode(option, value)
            except (TypeError, ValueError) as exc:
                failures.append(exc)
        if failures:
            raise TypeError(f"value does not match union {annotation!r}") from failures[-1]
        return value

    if annotation is bytes:
        if (
            not isinstance(value, Mapping)
            or value.get("encoding") != "hex"
            or not isinstance(value.get("value"), str)
        ):
            raise TypeError("bytes field requires canonical hex encoding")
        try:
            return bytes.fromhex(value["value"])
        except ValueError as exc:
            raise ValueError("invalid hex-encoded bytes") from exc

    if annotation is Path:
        if not isinstance(value, (str, Path)):
            raise TypeError("path field requires a string or Path")
        return Path(value)

    if isinstance(annotation, type) and is_dataclass(annotation):
        if isinstance(value, annotation):
            return value
        if not isinstance(value, Mapping):
            raise TypeError(f"{annotation.__name__} field requires an object")
        return _deserialize_type(annotation, value)

    if annotation is str:
        if not isinstance(value, str):
            raise TypeError("string field requires a string")
        return value

    if annotation is bool:
        if type(value) is not bool:
            raise TypeError("boolean field requires a boolean")
        return value

    if annotation is int:
        if type(value) is not int:
            raise TypeError("integer field requires an integer")
        return value

    if annotation is float:
        if type(value) is not float:
            raise TypeError("float field requires a floating-point number")
        return value

    if annotation is type(None):
        if value is not None:
            raise TypeError("null field requires null")
        return None

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {annotation.__name__} value") from exc

    if isinstance(annotation, type):
        if not isinstance(value, annotation):
            raise TypeError(
                f"field requires {annotation.__name__}, got {type(value).__name__}"
            )
        return value

    return value


def _deserialize_type(cls: type[T], payload: Mapping[str, Any]) -> T:
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema version: {version!r}")

    declared_fields = {field.name: field for field in fields(cls)}
    if isinstance(payload, _SerializedModelDict):
        supplied = set(payload._field_values)
    else:
        supplied = set(payload) - {"schema_version"}
    unknown = supplied - set(declared_fields)
    if unknown:
        raise ValueError(f"unknown fields for {cls.__name__}: {sorted(unknown)!r}")

    missing = {
        name
        for name, field in declared_fields.items()
        if name not in supplied
        and field.default is MISSING
        and field.default_factory is MISSING
    }
    if missing:
        raise ValueError(f"missing fields for {cls.__name__}: {sorted(missing)!r}")

    hints = get_type_hints(cls)
    values = {
        name: _decode(
            hints.get(name, Any),
            payload.declared_field_value(name)
            if isinstance(payload, _SerializedModelDict)
            else payload[name],
        )
        for name in supplied
    }
    return cls(**values)


def deserialize_model(model_name: str, payload: Mapping[str, Any]) -> Any:
    """Construct a declared Mother model from an exact schema-v1 mapping."""

    if model_name not in _MODEL_TYPES:
        raise ValueError(f"unknown Mother model: {model_name!r}")
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    return _deserialize_type(_MODEL_TYPES[model_name], payload)


def _validate_untyped(value: Any, field_name: str) -> None:
    if isinstance(value, (set, frozenset)):
        raise TypeError(f"{field_name} may not contain unordered set values")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{field_name} mapping keys must be strings")
            _validate_untyped(item, f"{field_name}[{key!r}]")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_untyped(item, f"{field_name}[{index}]")
        return
    if is_dataclass(value):
        _validate_declared_fields(value)


def _validate_value(annotation: Any, value: Any, field_name: str) -> None:
    if annotation is Any:
        _validate_untyped(value, field_name)
        return

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin in (UnionType,) or str(origin) == "typing.Union":
        for option in args:
            if option is type(None) and value is None:
                return
            if option is type(None):
                continue
            try:
                _validate_value(option, value, field_name)
                return
            except (TypeError, ValueError):
                pass
        raise TypeError(f"{field_name} does not match {annotation!r}")

    if origin is tuple:
        if not isinstance(value, tuple):
            raise TypeError(f"{field_name} must be a tuple")
        item_annotation = args[0] if args else Any
        for index, item in enumerate(value):
            _validate_value(item_annotation, item, f"{field_name}[{index}]")
        return

    if origin in (dict, Mapping, ABCMapping) or annotation in (
        dict,
        Mapping,
        ABCMapping,
        FrozenMapping,
    ):
        if not isinstance(value, Mapping):
            raise TypeError(f"{field_name} must be a mapping")
        key_annotation, item_annotation = args if len(args) == 2 else (str, Any)
        if key_annotation not in (str, Any):
            raise TypeError(f"{field_name} must use string keys")
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{field_name} mapping keys must be strings")
            _validate_value(item_annotation, item, f"{field_name}[{key!r}]")
        return

    if annotation is str:
        if type(value) is not str:
            raise TypeError(f"{field_name} must be a string")
        return
    if annotation is bool:
        if type(value) is not bool:
            raise TypeError(f"{field_name} must be a boolean")
        return
    if annotation is int:
        if type(value) is not int:
            raise TypeError(f"{field_name} must be an integer")
        return
    if annotation is float:
        if type(value) is not float:
            raise TypeError(f"{field_name} must be a floating-point number")
        return
    if annotation is bytes:
        if type(value) is not bytes:
            raise TypeError(f"{field_name} must be bytes")
        return
    if annotation is Path:
        if not isinstance(value, Path):
            raise TypeError(f"{field_name} must be a Path")
        return
    if annotation is type(None):
        if value is not None:
            raise TypeError(f"{field_name} must be null")
        return
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if not isinstance(value, annotation):
            raise TypeError(f"{field_name} must be {annotation.__name__}")
        return
    if isinstance(annotation, type) and is_dataclass(annotation):
        if not isinstance(value, annotation):
            raise TypeError(f"{field_name} must be {annotation.__name__}")
        _validate_declared_fields(value)
        return
    if isinstance(annotation, type) and not isinstance(value, annotation):
        raise TypeError(f"{field_name} must be {annotation.__name__}")

    _validate_untyped(value, field_name)


def _validate_declared_fields(instance: Any) -> None:
    hints = get_type_hints(type(instance))
    for field in fields(instance):
        _validate_value(
            hints.get(field.name, Any),
            getattr(instance, field.name),
            f"{type(instance).__name__}.{field.name}",
        )


def _install_constructor_validation(cls: type[Any]) -> None:
    original_init = cls.__init__

    @wraps(original_init)
    def validated_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        _validate_declared_fields(self)

    cls.__init__ = validated_init  # type: ignore[method-assign]


for _model_type in _MODEL_TYPES.values():
    _install_constructor_validation(_model_type)


__all__ = sorted((*_MODEL_TYPES, "FrozenMapping", "serialize_model", "deserialize_model"))
