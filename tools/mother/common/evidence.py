"""Typed evidence storage, redaction, and provenance-bound export."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import unicodedata
from typing import Any

from . import object_store
from .canonical import canonical_json
from .errors import MotherError
from .models import ContentHash, EvidenceRef, OperationIdentity

_MODULE_ID = "MOTHER-OFM-CORE-008"
_PRIVATE_KEYS = frozenset({
    "access_token", "api_token", "credential", "mnemonic", "password",
    "private_key", "private_key_bytes", "refresh_token", "secret",
    "secret_bytes", "seed",
})


def _nfc_text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{name} must be non-empty")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must already be NFC")
    return value


def _tuple_of(value: Any, member_type: type, name: str) -> tuple[Any, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    if any(type(item) is not member_type for item in value):
        raise TypeError(f"{name} contains an invalid member")
    return value


@dataclass(frozen=True, slots=True)
class EvidenceDocument:
    document_version: str
    schema_id: str
    source: str
    observation_time: str
    redaction_policy: str
    payload: bytes

    def __post_init__(self) -> None:
        if self.document_version != "evidence-document.v1":
            raise ValueError("unsupported evidence document version")
        for name in ("schema_id", "source", "observation_time", "redaction_policy"):
            _nfc_text(getattr(self, name), name)
        if type(self.payload) is not bytes:
            raise TypeError("payload must be bytes")


@dataclass(frozen=True, slots=True)
class RedactionRule:
    json_pointer: str

    def __post_init__(self) -> None:
        _nfc_text(self.json_pointer, "json_pointer")


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    policy_version: str
    policy_id: str
    rules: tuple[RedactionRule, ...]

    def __post_init__(self) -> None:
        if self.policy_version != "redaction-policy.v1":
            raise ValueError("unsupported redaction policy version")
        _nfc_text(self.policy_id, "policy_id")
        _tuple_of(self.rules, RedactionRule, "rules")


@dataclass(frozen=True, slots=True)
class EvidenceExportRequest:
    source_ref: EvidenceRef
    policy: RedactionPolicy

    def __post_init__(self) -> None:
        if type(self.source_ref) is not EvidenceRef:
            raise TypeError("source_ref must be an EvidenceRef")
        if type(self.policy) is not RedactionPolicy:
            raise TypeError("policy must be a RedactionPolicy")


@dataclass(frozen=True, slots=True)
class EvidenceManifestEntry:
    source_ref: EvidenceRef
    export_ref: EvidenceRef

    def __post_init__(self) -> None:
        if type(self.source_ref) is not EvidenceRef:
            raise TypeError("source_ref must be an EvidenceRef")
        if type(self.export_ref) is not EvidenceRef:
            raise TypeError("export_ref must be an EvidenceRef")


@dataclass(frozen=True, slots=True)
class EvidenceManifest:
    manifest_version: str
    entries: tuple[EvidenceManifestEntry, ...]

    def __post_init__(self) -> None:
        if self.manifest_version != "evidence-manifest.v1":
            raise ValueError("unsupported evidence manifest version")
        _tuple_of(self.entries, EvidenceManifestEntry, "entries")


@dataclass(frozen=True, slots=True)
class EvidenceExportResult:
    manifest: EvidenceManifest
    manifest_ref: EvidenceRef
    exported_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if type(self.manifest) is not EvidenceManifest:
            raise TypeError("manifest must be an EvidenceManifest")
        if type(self.manifest_ref) is not EvidenceRef:
            raise TypeError("manifest_ref must be an EvidenceRef")
        _tuple_of(self.exported_refs, EvidenceRef, "exported_refs")


def _operation(value: OperationIdentity) -> OperationIdentity:
    if type(value) is not OperationIdentity:
        raise TypeError("operation must be an OperationIdentity")
    return value


def _error(operation: OperationIdentity, code: str, message: str) -> MotherError:
    return MotherError(
        code=code,
        message=message,
        operation_id=operation.operation_id,
        module_id=_MODULE_ID,
        retry_class="never",
        authority_effect="none",
    )


def _is_nfc_tree(value: Any) -> bool:
    if type(value) is str:
        return unicodedata.normalize("NFC", value) == value
    if isinstance(value, dict):
        return all(
            type(k) is str
            and unicodedata.normalize("NFC", k) == k
            and _is_nfc_tree(v)
            for k, v in value.items()
        )
    if isinstance(value, list):
        return all(_is_nfc_tree(v) for v in value)
    return value is None or type(value) in (bool, int)


def _decode_canonical_object(data: bytes, operation: OperationIdentity, code: str) -> dict[str, Any]:
    try:
        if type(data) is not bytes:
            raise TypeError
        value = json.loads(data.decode("utf-8"))
        if type(value) is not dict or not _is_nfc_tree(value):
            raise ValueError
        if canonical_json(value) != data:
            raise ValueError
        return value
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, OverflowError):
        raise _error(operation, code, "canonical JSON object is malformed") from None


def _valid_ref(reference: Any) -> bool:
    if type(reference) is not EvidenceRef:
        return False
    h = reference.object_hash
    if type(h) is not ContentHash or h.algorithm != "sha256":
        return False
    if type(h.digest) is not str or len(h.digest) != 64 or any(c not in "0123456789abcdef" for c in h.digest):
        return False
    for value in (
        reference.schema, reference.redaction_policy,
        reference.source, reference.observation_time,
    ):
        if type(value) is not str or not value or unicodedata.normalize("NFC", value) != value:
            return False
    return True


def _hash_wire(value: ContentHash) -> dict[str, Any]:
    return {"algorithm": value.algorithm, "digest": value.digest, "schema_version": 1}


def _ref_wire(value: EvidenceRef) -> dict[str, Any]:
    return {
        "object_hash": _hash_wire(value.object_hash),
        "observation_time": value.observation_time,
        "redaction_policy": value.redaction_policy,
        "schema": value.schema,
        "schema_version": 1,
        "source": value.source,
    }


def _parse_hash(value: Any) -> ContentHash:
    if type(value) is not dict or set(value) != {"schema_version", "algorithm", "digest"}:
        raise ValueError
    if value["schema_version"] != 1 or value["algorithm"] != "sha256":
        raise ValueError
    return ContentHash("sha256", value["digest"])


def _parse_ref(value: Any) -> EvidenceRef:
    if type(value) is not dict or set(value) != {
        "schema_version", "object_hash", "schema", "redaction_policy",
        "source", "observation_time",
    }:
        raise ValueError
    if value["schema_version"] != 1:
        raise ValueError
    ref = EvidenceRef(
        _parse_hash(value["object_hash"]),
        value["schema"],
        value["redaction_policy"],
        value["source"],
        value["observation_time"],
    )
    if not _valid_ref(ref):
        raise ValueError
    return ref


def _ref_key(ref: EvidenceRef) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    return tuple(
        value.encode("utf-8")
        for value in (
            ref.object_hash.digest, ref.schema, ref.redaction_policy,
            ref.source, ref.observation_time,
        )
    )  # type: ignore[return-value]


def _envelope(document: EvidenceDocument, operation: OperationIdentity) -> bytes:
    payload = _decode_canonical_object(
        document.payload, operation, "MOTHER_EVIDENCE_MALFORMED_DOCUMENT"
    )
    return canonical_json({
        "document_version": document.document_version,
        "observation_time": document.observation_time,
        "payload": payload,
        "redaction_policy": document.redaction_policy,
        "schema_id": document.schema_id,
        "source": document.source,
    })


def store_evidence(
    root: Path,
    document: EvidenceDocument,
    *,
    operation: OperationIdentity,
) -> EvidenceRef:
    operation = _operation(operation)
    if not isinstance(root, Path):
        raise TypeError("root must be a Path")
    if type(document) is not EvidenceDocument:
        raise TypeError("document must be an EvidenceDocument")
    try:
        for name in ("schema_id", "source", "observation_time", "redaction_policy"):
            _nfc_text(getattr(document, name), name)
        data = _envelope(document, operation)
    except MotherError:
        raise
    except (TypeError, ValueError):
        raise _error(operation, "MOTHER_EVIDENCE_MALFORMED_DOCUMENT", "evidence document is malformed") from None
    digest = object_store.put_immutable(root, data, operation=operation)
    return EvidenceRef(
        digest, document.schema_id, document.redaction_policy,
        document.source, document.observation_time,
    )


def load_evidence(
    root: Path,
    reference: EvidenceRef,
    *,
    operation: OperationIdentity,
) -> EvidenceDocument:
    operation = _operation(operation)
    if not isinstance(root, Path):
        raise TypeError("root must be a Path")
    if not _valid_ref(reference):
        raise _error(operation, "MOTHER_EVIDENCE_REFERENCE_MISMATCH", "evidence reference is invalid")
    raw = object_store.get_verified(root, reference.object_hash, operation=operation)
    envelope = _decode_canonical_object(
        raw, operation, "MOTHER_EVIDENCE_MALFORMED_DOCUMENT"
    )
    expected = {
        "document_version", "observation_time", "payload",
        "redaction_policy", "schema_id", "source",
    }
    try:
        if set(envelope) != expected:
            raise ValueError
        payload = canonical_json(envelope["payload"])
        document = EvidenceDocument(
            envelope["document_version"], envelope["schema_id"], envelope["source"],
            envelope["observation_time"], envelope["redaction_policy"], payload,
        )
    except (TypeError, ValueError):
        raise _error(operation, "MOTHER_EVIDENCE_MALFORMED_DOCUMENT", "stored evidence envelope is malformed") from None
    if (
        document.schema_id != reference.schema
        or document.redaction_policy != reference.redaction_policy
        or document.source != reference.source
        or document.observation_time != reference.observation_time
    ):
        raise _error(operation, "MOTHER_EVIDENCE_REFERENCE_MISMATCH", "reference metadata does not match stored evidence")
    return document


def _normalize_pointer(pointer: str) -> tuple[str, tuple[str, ...]]:
    _nfc_text(pointer, "json_pointer")
    if not pointer.startswith("/"):
        raise ValueError
    tokens: list[str] = []
    for raw in pointer.split("/")[1:]:
        decoded: list[str] = []
        i = 0
        while i < len(raw):
            if raw[i] == "~":
                if i + 1 >= len(raw) or raw[i + 1] not in "01":
                    raise ValueError
                decoded.append("~" if raw[i + 1] == "0" else "/")
                i += 2
            else:
                decoded.append(raw[i])
                i += 1
        token = "".join(decoded)
        _nfc_text(token, "json_pointer token", allow_empty=True)
        tokens.append(token)
    encoded = "/" + "/".join(t.replace("~", "~0").replace("/", "~1") for t in tokens)
    return encoded, tuple(tokens)


def _resolve_parent(root: Any, tokens: tuple[str, ...]) -> tuple[Any, str | int]:
    current = root
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                raise ValueError
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                raise ValueError
            index = int(token)
            if index >= len(current):
                raise ValueError
            current = current[index]
        else:
            raise ValueError
    final = tokens[-1]
    if isinstance(current, dict):
        if final not in current:
            raise ValueError
        return current, final
    if isinstance(current, list):
        if not final.isdigit() or (len(final) > 1 and final.startswith("0")):
            raise ValueError
        index = int(final)
        if index >= len(current):
            raise ValueError
        return current, index
    raise ValueError


def redact_copy(
    document: EvidenceDocument,
    policy: RedactionPolicy,
    *,
    operation: OperationIdentity,
) -> EvidenceDocument:
    operation = _operation(operation)
    if type(document) is not EvidenceDocument or type(policy) is not RedactionPolicy:
        raise TypeError("document and policy must use exact types")
    try:
        payload = _decode_canonical_object(
            document.payload, operation, "MOTHER_EVIDENCE_MALFORMED_DOCUMENT"
        )
        normalized = [_normalize_pointer(rule.json_pointer) for rule in policy.rules]
        token_paths = [tokens for _, tokens in normalized]
        for i, left in enumerate(token_paths):
            for right in token_paths[i + 1:]:
                if left == right or left == right[:len(left)] or right == left[:len(right)]:
                    raise ValueError
        # Resolve all against original before mutating.
        for tokens in token_paths:
            if not tokens:
                raise ValueError
            _resolve_parent(payload, tokens)
        result = deepcopy(payload)
        for _, tokens in sorted(normalized, key=lambda item: item[0].encode("utf-8")):
            parent, key = _resolve_parent(result, tokens)
            parent[key] = "[REDACTED]"
        return EvidenceDocument(
            "evidence-document.v1", document.schema_id, document.source,
            document.observation_time, policy.policy_id, canonical_json(result),
        )
    except MotherError:
        raise
    except (TypeError, ValueError, IndexError):
        raise _error(operation, "MOTHER_EVIDENCE_REDACTION_FAILED", "evidence redaction failed") from None


def _contains_private_material(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _PRIVATE_KEYS and item != "[REDACTED]":
                return True
            if _contains_private_material(item):
                return True
    elif isinstance(value, list):
        return any(_contains_private_material(item) for item in value)
    return False


def _validate_export_document(document: EvidenceDocument, operation: OperationIdentity) -> None:
    payload = _decode_canonical_object(
        document.payload, operation, "MOTHER_EVIDENCE_MALFORMED_DOCUMENT"
    )
    if document.redaction_policy == "none" or _contains_private_material(payload):
        raise _error(operation, "MOTHER_EVIDENCE_PRIVATE_MATERIAL", "export contains private material")


def _roots_overlap(left: Path, right: Path) -> bool:
    a = left.resolve(strict=False)
    b = right.resolve(strict=False)
    return a == b or a in b.parents or b in a.parents


def export_manifest(
    source_root: Path,
    export_root: Path,
    requests: tuple[EvidenceExportRequest, ...],
    manifest_time: str,
    *,
    operation: OperationIdentity,
) -> EvidenceExportResult:
    operation = _operation(operation)
    if not isinstance(source_root, Path) or not isinstance(export_root, Path):
        raise TypeError("roots must be Path values")
    if _roots_overlap(source_root, export_root):
        raise _error(operation, "MOTHER_INPUT_OVERLAPPING_STORAGE_ROOTS", "source and export roots overlap")
    if type(requests) is not tuple or any(type(r) is not EvidenceExportRequest for r in requests):
        raise TypeError("requests must be a tuple of EvidenceExportRequest")
    try:
        _nfc_text(manifest_time, "manifest_time")
    except (TypeError, ValueError):
        raise _error(operation, "MOTHER_EVIDENCE_MALFORMED_MANIFEST", "manifest time is invalid") from None
    seen: set[EvidenceRef] = set()
    for request in requests:
        if not _valid_ref(request.source_ref):
            raise _error(operation, "MOTHER_EVIDENCE_REFERENCE_MISMATCH", "source reference is invalid")
        if request.source_ref in seen:
            raise _error(operation, "MOTHER_EVIDENCE_DUPLICATE_EXPORT", "duplicate source reference")
        seen.add(request.source_ref)

    entries: list[EvidenceManifestEntry] = []
    for request in sorted(requests, key=lambda r: _ref_key(r.source_ref)):
        source = load_evidence(source_root, request.source_ref, operation=operation)
        exported = redact_copy(source, request.policy, operation=operation)
        _validate_export_document(exported, operation)
        export_ref = store_evidence(export_root, exported, operation=operation)
        entries.append(EvidenceManifestEntry(request.source_ref, export_ref))

    manifest = EvidenceManifest("evidence-manifest.v1", tuple(entries))
    payload = canonical_json({
        "entries": [
            {"export_ref": _ref_wire(e.export_ref), "source_ref": _ref_wire(e.source_ref)}
            for e in entries
        ],
        "manifest_version": "evidence-manifest.v1",
    })
    manifest_doc = EvidenceDocument(
        "evidence-document.v1", "mother.evidence-manifest.v1",
        "MOTHER-OFM-CORE-008.export_manifest", manifest_time, "manifest", payload,
    )
    manifest_ref = store_evidence(export_root, manifest_doc, operation=operation)
    exported_refs = tuple(sorted({e.export_ref for e in entries}, key=_ref_key))
    return EvidenceExportResult(manifest, manifest_ref, exported_refs)


def _valid_manifest_ref(reference: Any) -> bool:
    return (
        _valid_ref(reference)
        and reference.schema == "mother.evidence-manifest.v1"
        and reference.redaction_policy == "manifest"
        and reference.source == "MOTHER-OFM-CORE-008.export_manifest"
    )


def load_export_result(
    export_root: Path,
    manifest_ref: EvidenceRef,
    *,
    operation: OperationIdentity,
) -> EvidenceExportResult:
    operation = _operation(operation)
    if not isinstance(export_root, Path):
        raise TypeError("export_root must be a Path")
    if not _valid_manifest_ref(manifest_ref):
        raise _error(operation, "MOTHER_EVIDENCE_REFERENCE_MISMATCH", "manifest reference metadata is invalid")
    document = load_evidence(export_root, manifest_ref, operation=operation)
    if (
        document.schema_id != "mother.evidence-manifest.v1"
        or document.redaction_policy != "manifest"
        or document.source != "MOTHER-OFM-CORE-008.export_manifest"
    ):
        raise _error(operation, "MOTHER_EVIDENCE_REFERENCE_MISMATCH", "manifest reference does not select a manifest")
    payload = _decode_canonical_object(
        document.payload, operation, "MOTHER_EVIDENCE_MALFORMED_MANIFEST"
    )
    try:
        if set(payload) != {"entries", "manifest_version"}:
            raise ValueError
        if payload["manifest_version"] != "evidence-manifest.v1" or type(payload["entries"]) is not list:
            raise ValueError
        entries: list[EvidenceManifestEntry] = []
        for row in payload["entries"]:
            if type(row) is not dict or set(row) != {"source_ref", "export_ref"}:
                raise ValueError
            entries.append(EvidenceManifestEntry(_parse_ref(row["source_ref"]), _parse_ref(row["export_ref"])))
    except (TypeError, ValueError, KeyError):
        raise _error(operation, "MOTHER_EVIDENCE_MALFORMED_MANIFEST", "stored evidence manifest is malformed") from None

    source_refs = [e.source_ref for e in entries]
    if len(set(source_refs)) != len(source_refs):
        raise _error(operation, "MOTHER_EVIDENCE_DUPLICATE_EXPORT", "manifest repeats a source reference")
    if source_refs != sorted(source_refs, key=_ref_key):
        raise _error(operation, "MOTHER_EVIDENCE_MALFORMED_MANIFEST", "manifest entries are not canonically ordered")

    for entry in entries:
        exported = load_evidence(export_root, entry.export_ref, operation=operation)
        _validate_export_document(exported, operation)

    manifest = EvidenceManifest("evidence-manifest.v1", tuple(entries))
    exported_refs = tuple(sorted({e.export_ref for e in entries}, key=_ref_key))
    return EvidenceExportResult(manifest, manifest_ref, exported_refs)


__all__ = [
    "EvidenceDocument", "RedactionRule", "RedactionPolicy",
    "EvidenceExportRequest", "EvidenceManifestEntry", "EvidenceManifest",
    "EvidenceExportResult", "store_evidence", "load_evidence", "redact_copy",
    "export_manifest", "load_export_result",
]
