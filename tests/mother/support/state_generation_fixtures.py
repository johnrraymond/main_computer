from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tools.mother.common.canonical import canonical_json, canonical_yaml
from tools.mother.common.hashing import ordered_root, sha256
from tools.mother.common.models import ContentHash, HeadTuple, OperationIdentity


PRIVATE_STATE_KIND = "main_computer.mother.private_state.v1"
PRIVATE_METADATA_KIND = "main_computer.mother.private_state_metadata.v1"
PRIVATE_MANIFEST_VERSION = "main_computer.mother.private_recovery_manifest.v1"
GENERATION_DESCRIPTOR_VERSION = "mother.state-generation-descriptor.v1"
GENERATION_MANIFEST_VERSION = "mother.state-generation-manifest.v1"
GENERATION_POINTER_VERSION = "mother.state-generation-pointer.v1"


def operation(
    kind: str,
    *,
    network: str = "testnet",
    operation_id: str | None = None,
) -> OperationIdentity:
    return OperationIdentity(
        operation_id=operation_id or f"state-wave2b-{kind.lower()}",
        request_id="state-wave2b-request",
        network=network,
        operation_kind=kind,
    )


def hash_wire(value: ContentHash) -> dict[str, object]:
    return {
        "algorithm": value.algorithm,
        "digest": value.digest,
        "schema_version": 1,
    }


def head(tag: str = "source", *, sequence: int = 4) -> HeadTuple:
    return HeadTuple(
        journal_identity="network-journal",
        sequence=sequence,
        entry_hash=sha256(f"{tag}-entry".encode()),
        authorization_bundle_hash=sha256(f"{tag}-bundle".encode()),
        state_hash=sha256(f"{tag}-state".encode()),
        head_id=f"{tag}-head",
        head_epoch=2,
    )


def head_wire(value: HeadTuple) -> dict[str, object]:
    return {
        "authorization_bundle_hash": hash_wire(value.authorization_bundle_hash),
        "entry_hash": hash_wire(value.entry_hash),
        "head_epoch": value.head_epoch,
        "head_id": value.head_id,
        "journal_identity": value.journal_identity,
        "sequence": value.sequence,
        "state_hash": hash_wire(value.state_hash),
    }


def binding_wire(value: Any) -> dict[str, object]:
    return {
        "content_hash": hash_wire(value.content_hash),
        "generation": value.generation,
        "private_state_kind": value.private_state_kind,
        "recovery_manifest_hash": hash_wire(value.recovery_manifest_hash),
    }


def valid_private_document(
    *,
    network: str = "testnet",
    node_id: str = "node-a",
    validator_id: str = "validator-a",
    address_byte: str = "11",
    key_byte: str = "22",
) -> dict[str, object]:
    return {
        "kind": PRIVATE_STATE_KIND,
        "networks": {
            network: {
                "nodes": {
                    node_id: {
                        "validator_ref": (
                            f"networks.{network}.validators.{validator_id}"
                        )
                    }
                },
                "validators": {
                    validator_id: {
                        "address": "0x" + address_byte * 20,
                        "private_key": "0x" + key_byte * 32,
                    }
                },
            }
        },
        "schema_version": 1,
    }


def _chmod_private_tree(paths: Any) -> None:
    if os.name == "nt":
        return
    directories = {
        paths.root,
        paths.recovery_objects_root.parent,
        paths.recovery_objects_root,
    }
    for directory in directories:
        if directory.exists():
            directory.chmod(0o700)
    for path in paths.root.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)
        elif path.is_file():
            path.chmod(0o600)


def private_material(
    models: Any,
    *,
    generation: int = 1,
    document: dict[str, object] | None = None,
    recovery_payloads: tuple[tuple[str, bytes], ...] = (
        ("keys/validator-a.bin", b"private-recovery-material-a"),
        ("shares/recovery-01.bin", b"private-recovery-material-b"),
    ),
    previous_content_hash: ContentHash | None = None,
    operation_id: str = "state-wave2b-install-private",
) -> dict[str, Any]:
    document_value = document or valid_private_document()
    document_bytes = canonical_yaml(document_value)
    canonical_object_bytes = canonical_json(document_value)
    content_hash = sha256(document_bytes)

    entries: list[dict[str, object]] = []
    objects: list[dict[str, Any]] = []
    for relative_path, payload in sorted(
        recovery_payloads,
        key=lambda item: item[0].encode("utf-8"),
    ):
        content = sha256(payload)
        entries.append(
            {
                "byte_length": len(payload),
                "content_hash": hash_wire(content),
                "generation": generation,
                "relative_path": relative_path,
            }
        )
        objects.append(
            {
                "relative_path": relative_path,
                "generation": generation,
                "content_hash": content,
                "payload": payload,
            }
        )

    manifest_wire = {
        "entries": entries,
        "manifest_version": PRIVATE_MANIFEST_VERSION,
        "private_state_generation": generation,
    }
    manifest_bytes = canonical_json(manifest_wire)
    manifest_hash = sha256(manifest_bytes)
    if generation == 1:
        previous = None
    else:
        previous = previous_content_hash or sha256(b"previous-private-state")
    metadata_wire = {
        "content_hash": hash_wire(content_hash),
        "generation": generation,
        "kind": PRIVATE_METADATA_KIND,
        "previous_content_hash": None if previous is None else hash_wire(previous),
        "private_state_kind": PRIVATE_STATE_KIND,
        "recovery_manifest_hash": hash_wire(manifest_hash),
        "updated_at": "2026-07-30T12:53:51Z",
        "updated_by_action_id": operation_id,
    }
    metadata_bytes = canonical_json(metadata_wire)
    binding = models.PrivateStateBinding(
        private_state_kind=PRIVATE_STATE_KIND,
        generation=generation,
        content_hash=content_hash,
        recovery_manifest_hash=manifest_hash,
    )
    closure_members = [
        sha256(document_bytes),
        sha256(metadata_bytes),
        sha256(manifest_bytes),
    ]
    for entry in entries:
        closure_members.append(sha256(canonical_json(entry)))
    return {
        "document": document_value,
        "document_bytes": document_bytes,
        "canonical_object_bytes": canonical_object_bytes,
        "metadata_wire": metadata_wire,
        "metadata_bytes": metadata_bytes,
        "manifest_wire": manifest_wire,
        "manifest_bytes": manifest_bytes,
        "objects": tuple(objects),
        "binding": binding,
        "closure_hash": ordered_root(closure_members),
    }


def write_private_material(paths: Any, material: dict[str, Any]) -> None:
    paths.recovery_objects_root.mkdir(parents=True, exist_ok=True)
    for item in material["objects"]:
        target = paths.recovery_objects_root / item["relative_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item["payload"])
    paths.identity_file.write_bytes(material["document_bytes"])
    paths.metadata_file.write_bytes(material["metadata_bytes"])
    paths.recovery_manifest.write_bytes(material["manifest_bytes"])
    _chmod_private_tree(paths)


def generation_descriptor_wire(
    *,
    generation_id: str,
    generation_kind: str,
    network: str,
    owner_operation_id: str,
    source_head: HeadTuple | None,
    private_state: Any,
    active_pointer_predecessor: ContentHash | None,
) -> dict[str, object]:
    return {
        "active_pointer_predecessor": (
            None
            if active_pointer_predecessor is None
            else hash_wire(active_pointer_predecessor)
        ),
        "descriptor_version": GENERATION_DESCRIPTOR_VERSION,
        "generation_id": generation_id,
        "generation_kind": generation_kind,
        "network": network,
        "owner_operation_id": owner_operation_id,
        "private_state": binding_wire(private_state),
        "source_head": None if source_head is None else head_wire(source_head),
    }


def generation_entry_wire(
    relative_path: str,
    payload: bytes,
) -> dict[str, object]:
    return {
        "byte_length": len(payload),
        "content_hash": hash_wire(sha256(payload)),
        "relative_path": relative_path,
    }


def activation_pointer_wire(
    *,
    network: str,
    activation: Any,
) -> dict[str, object]:
    predecessor = getattr(activation, "expected_pointer", None)
    predecessor_hash = None if predecessor is None else sha256(predecessor)
    return {
        "activation_record_hash": hash_wire(activation.activation_record_hash),
        "active_pointer_predecessor": (
            None if predecessor_hash is None else hash_wire(predecessor_hash)
        ),
        "generation_id": activation.generation_id,
        "immutable_root": hash_wire(activation.immutable_root),
        "manifest_hash": hash_wire(activation.manifest_hash),
        "network": network,
        "pointer_version": GENERATION_POINTER_VERSION,
        "private_state": binding_wire(activation.private_state),
    }


def make_private_closure(private_state: Any, models: Any, material: dict[str, Any], paths: Any):
    objects = tuple(
        private_state.PrivateRecoveryObject(
            relative_path=item["relative_path"],
            generation=item["generation"],
            content_hash=item["content_hash"],
            payload=item["payload"],
        )
        for item in material["objects"]
    )
    return private_state.PrivateRecoveryClosure(
        source_paths=paths,
        document_bytes=material["document_bytes"],
        metadata_bytes=material["metadata_bytes"],
        recovery_manifest_bytes=material["manifest_bytes"],
        recovery_objects=objects,
        binding=material["binding"],
        closure_hash=material["closure_hash"],
    )
