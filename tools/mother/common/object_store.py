"""Hash-addressed immutable object storage and verified closure copying."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import os
from pathlib import Path
from typing import Any

from . import atomic_files, hashing
from .errors import MotherError
from .models import ContentHash, DurableEffectRef, OperationIdentity


_MODULE_ID = "MOTHER-OFM-CORE-012"


def _operation(value: OperationIdentity) -> OperationIdentity:
    if not isinstance(value, OperationIdentity):
        raise TypeError("operation must be an OperationIdentity")
    return value


def _mother_error(
    operation: OperationIdentity,
    code: str,
    message: str,
    *,
    retry_class: str,
    authority_effect: str = "none",
    durable_effect_refs: tuple[DurableEffectRef, ...] = (),
    cause: BaseException | None = None,
) -> MotherError:
    return MotherError(
        code=code,
        message=message,
        operation_id=operation.operation_id,
        module_id=_MODULE_ID,
        retry_class=retry_class,
        authority_effect=authority_effect,
        durable_effect_refs=durable_effect_refs,
        cause_class="" if cause is None else type(cause).__name__,
    )


def _as_path(
    value: str | os.PathLike[str],
    name: str,
    operation: OperationIdentity,
) -> Path:
    if isinstance(value, str):
        if "\x00" in value:
            raise _mother_error(
                operation,
                "MOTHER_INPUT_UNSAFE_PATH",
                f"{name} contains a NUL byte",
                retry_class="never",
            )
        path = Path(value)
    elif isinstance(value, os.PathLike):
        path = Path(value)
    else:
        raise TypeError(f"{name} must be a string or path-like value")
    return path.absolute()


def _first_symlink(path: Path) -> Path | None:
    parts = path.parts
    if not parts:
        return None
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return current
        except OSError:
            return current
    return None


def _unsafe_path(
    operation: OperationIdentity,
    message: str,
    cause: BaseException | None = None,
) -> MotherError:
    return _mother_error(
        operation,
        "MOTHER_INPUT_UNSAFE_PATH",
        message,
        retry_class="never",
        cause=cause,
    )


def _validate_no_symlink(path: Path, operation: OperationIdentity) -> None:
    unsafe = _first_symlink(path)
    if unsafe is not None:
        raise _unsafe_path(operation, f"unsafe symlink path component: {unsafe}")


def _root(
    value: str | os.PathLike[str],
    *,
    create: bool,
    operation: OperationIdentity,
) -> Path:
    root = _as_path(value, "object-store root", operation)
    _validate_no_symlink(root, operation)
    if root.exists() and not root.is_dir():
        raise _unsafe_path(
            operation,
            f"object-store root is not a directory: {root}",
        )
    if create:
        atomic_files.ensure_durable_directory(
            root,
            operation=operation,
            reconcile_existing=True,
        )
        _validate_no_symlink(root, operation)
    return root


def _content_hash(value: ContentHash, name: str = "reference") -> ContentHash:
    if not isinstance(value, ContentHash):
        raise TypeError(f"{name} must be ContentHash")
    return ContentHash(algorithm=value.algorithm, digest=value.digest)


def _object_path(
    root: Path,
    reference: ContentHash,
    operation: OperationIdentity,
) -> Path:
    path = root / reference.algorithm / reference.digest[:2] / reference.digest
    _validate_no_symlink(path, operation)
    return path


def _payload(value: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes-like")
    return bytes(value)


def _object_effect(path: Path, reference: ContentHash) -> DurableEffectRef:
    return DurableEffectRef(
        effect_kind="immutable-object-publication",
        target=str(path),
        content_hash=reference,
    )


def _object_corrupt(
    operation: OperationIdentity,
    message: str,
    cause: BaseException | None = None,
) -> MotherError:
    return _mother_error(
        operation,
        "MOTHER_STATE_OBJECT_CORRUPT",
        message,
        retry_class="never",
        cause=cause,
    )


def _durability_unconfirmed(
    operation: OperationIdentity,
    path: Path,
    reference: ContentHash,
    cause: BaseException,
) -> MotherError:
    return _mother_error(
        operation,
        "MOTHER_STATE_DURABILITY_UNCONFIRMED",
        "immutable object exists but parent-directory durability is unconfirmed",
        retry_class="after-reobserve",
        authority_effect="local-pointer-determined",
        durable_effect_refs=(_object_effect(path, reference),),
        cause=cause,
    )


def _ensure_object_hierarchy(
    root: Path,
    reference: ContentHash,
    operation: OperationIdentity,
) -> Path:
    algorithm_dir = root / reference.algorithm
    prefix_dir = algorithm_dir / reference.digest[:2]
    for directory in (root, algorithm_dir, prefix_dir):
        atomic_files.ensure_durable_directory(
            directory,
            operation=operation,
            reconcile_existing=True,
        )
        _validate_no_symlink(directory, operation)
    return prefix_dir / reference.digest


def _reconcile_existing_hierarchy(
    root: Path,
    reference: ContentHash,
    operation: OperationIdentity,
) -> None:
    for directory in (
        root,
        root / reference.algorithm,
        root / reference.algorithm / reference.digest[:2],
    ):
        _validate_no_symlink(directory, operation)
        if not directory.exists():
            return
        if not directory.is_dir():
            raise _unsafe_path(
                operation,
                f"object-store hierarchy component is not a directory: {directory}",
            )
        try:
            atomic_files.flush_directory(directory.parent)
        except OSError as exc:
            raise _mother_error(
                operation,
                "MOTHER_STATE_DURABILITY_UNCONFIRMED",
                f"object-store directory ancestry durability is unconfirmed: {directory}",
                retry_class="after-reobserve",
                authority_effect="local-pointer-determined",
                durable_effect_refs=(
                    DurableEffectRef(
                        effect_kind="local-directory-creation",
                        target=str(directory),
                        content_hash=hashing.sha256(b"directory"),
                    ),
                ),
                cause=exc,
            ) from exc


def _read_verified_locked(
    path: Path,
    reference: ContentHash,
    operation: OperationIdentity,
    *,
    expected: bytes | None = None,
) -> bytes:
    with atomic_files._exclusive_target_lock(path, operation):
        _validate_no_symlink(path, operation)
        if not path.exists():
            raise _mother_error(
                operation,
                "MOTHER_STATE_OBJECT_MISSING",
                "requested immutable object is absent",
                retry_class="after-reobserve",
            )
        if not path.is_file():
            raise _unsafe_path(
                operation,
                "content-addressed object path is not a regular file",
            )
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise _mother_error(
                operation,
                "MOTHER_STATE_OBJECT_MISSING",
                "requested immutable object disappeared during verification",
                retry_class="after-reobserve",
                cause=exc,
            ) from exc
        except OSError as exc:
            raise _mother_error(
                operation,
                "MOTHER_STATE_DURABLE_READ_FAILED",
                "failed to read the immutable object",
                retry_class="after-reobserve",
                cause=exc,
            ) from exc

        actual = hashing.sha256(data)
        if actual != reference:
            raise _object_corrupt(
                operation,
                "content-addressed object bytes do not match their declared hash",
            )
        if expected is not None and data != expected:
            raise _object_corrupt(
                operation,
                "existing content-addressed path contains different exact bytes",
            )
        try:
            atomic_files.flush_directory(path.parent)
        except OSError as exc:
            raise _durability_unconfirmed(operation, path, reference, exc) from exc
        return data


def put_immutable(
    root: str | os.PathLike[str],
    data: bytes | bytearray | memoryview,
    *,
    operation: OperationIdentity,
    faultpoints: Any = None,
) -> ContentHash:
    """Publish exact bytes at their SHA-256 address, idempotently."""

    op = _operation(operation)
    payload = _payload(data)
    reference = hashing.sha256(payload)
    store_root = _root(root, create=True, operation=op)
    path = _ensure_object_hierarchy(store_root, reference, op)
    _validate_no_symlink(path, op)

    try:
        atomic_files.durable_create(
            path,
            payload,
            operation=op,
            faultpoints=faultpoints,
        )
    except MotherError as exc:
        if exc.code == "MOTHER_CONFLICT_DURABLE_TARGET_EXISTS":
            _read_verified_locked(
                path,
                reference,
                op,
                expected=payload,
            )
            return reference
        if exc.code == "MOTHER_STATE_DURABILITY_UNCONFIRMED":
            raise _durability_unconfirmed(op, path, reference, exc) from exc
        raise
    return reference


def get_verified(
    root: str | os.PathLike[str],
    reference: ContentHash,
    *,
    operation: OperationIdentity,
) -> bytes:
    """Read exact object bytes only after synchronized durability verification."""

    op = _operation(operation)
    ref = _content_hash(reference)
    store_root = _root(root, create=False, operation=op)
    if not store_root.exists():
        raise _mother_error(
            op,
            "MOTHER_STATE_OBJECT_MISSING",
            "requested immutable object is absent",
            retry_class="after-reobserve",
        )
    _reconcile_existing_hierarchy(store_root, ref, op)
    path = _object_path(store_root, ref, op)
    return _read_verified_locked(path, ref, op)


def _references_tuple(
    values: Iterable[ContentHash],
    name: str,
    operation: OperationIdentity,
) -> tuple[ContentHash, ...]:
    if isinstance(
        values,
        (str, bytes, bytearray, memoryview, Mapping, set, frozenset),
    ):
        raise TypeError(f"{name} must be an ordered iterable of ContentHash values")
    result = tuple(_content_hash(value, name) for value in values)
    if len(set(result)) != len(result):
        raise _mother_error(
            operation,
            "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER",
            f"{name} contains duplicate content identities",
            retry_class="never",
        )
    return result


def _normalized_graph(
    references: Mapping[ContentHash, Iterable[ContentHash]],
    operation: OperationIdentity,
) -> dict[ContentHash, tuple[ContentHash, ...]]:
    if not isinstance(references, Mapping):
        raise TypeError("references must be a mapping")
    graph: dict[ContentHash, tuple[ContentHash, ...]] = {}
    for raw_parent, raw_children in references.items():
        parent = _content_hash(raw_parent, "reference graph key")
        if parent in graph:
            raise _mother_error(
                operation,
                "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER",
                "reference graph contains duplicate parent identities",
                retry_class="never",
            )
        graph[parent] = _references_tuple(
            raw_children,
            f"children of {parent.digest}",
            operation,
        )
    return graph


def _invalid_closure(
    operation: OperationIdentity,
    message: str,
    cause: BaseException | None = None,
) -> MotherError:
    return _mother_error(
        operation,
        "MOTHER_RECOVERY_INVALID_CLOSURE",
        message,
        retry_class="never",
        cause=cause,
    )


def _closure_order(
    roots: tuple[ContentHash, ...],
    graph: Mapping[ContentHash, tuple[ContentHash, ...]],
    operation: OperationIdentity,
) -> tuple[ContentHash, ...]:
    order: list[ContentHash] = []
    state: dict[ContentHash, int] = {}

    def visit(node: ContentHash) -> None:
        status = state.get(node, 0)
        if status == 1:
            raise _invalid_closure(operation, "closure graph contains a cycle")
        if status == 2:
            return
        children = graph.get(node)
        if children is None:
            raise _invalid_closure(
                operation,
                "closure graph is missing a referenced row",
            )
        state[node] = 1
        order.append(node)
        for child in children:
            visit(child)
        state[node] = 2

    for root in roots:
        visit(root)
    return tuple(order)


def verify_closure(
    root: str | os.PathLike[str],
    *,
    operation: OperationIdentity,
    roots: Iterable[ContentHash],
    references: Mapping[ContentHash, Iterable[ContentHash]],
    expected_members: Iterable[ContentHash],
) -> tuple[ContentHash, ...]:
    """Verify an exact, acyclic, fully reachable transitive object closure."""

    op = _operation(operation)
    store_root = _root(root, create=False, operation=op)
    root_refs = _references_tuple(roots, "roots", op)
    expected = _references_tuple(expected_members, "expected_members", op)
    graph = _normalized_graph(references, op)

    if not root_refs:
        if expected or graph:
            raise _invalid_closure(
                op,
                "empty roots do not describe the supplied closure",
            )
        return ()

    order = _closure_order(root_refs, graph, op)
    reachable = set(order)
    if reachable != set(expected):
        raise _invalid_closure(
            op,
            "reachable closure members do not equal the exact expected member set",
        )
    if reachable != set(graph):
        raise _invalid_closure(
            op,
            "reference graph contains unreachable or omitted rows",
        )

    for reference in order:
        try:
            get_verified(store_root, reference, operation=op)
        except MotherError as exc:
            if exc.code == "MOTHER_INPUT_UNSAFE_PATH":
                raise
            if exc.code in {
                "MOTHER_STATE_OBJECT_MISSING",
                "MOTHER_STATE_OBJECT_CORRUPT",
                "MOTHER_STATE_DURABLE_READ_FAILED",
            }:
                raise _invalid_closure(
                    op,
                    "closure contains a missing or corrupt immutable object",
                    exc,
                ) from exc
            raise
    return order


def _validate_distinct_roots(
    source: Path,
    destination: Path,
    operation: OperationIdentity,
) -> None:
    source_resolved = source.resolve(strict=False)
    destination_resolved = destination.resolve(strict=False)
    try:
        destination_resolved.relative_to(source_resolved)
    except ValueError:
        destination_inside = False
    else:
        destination_inside = True
    try:
        source_resolved.relative_to(destination_resolved)
    except ValueError:
        source_inside = False
    else:
        source_inside = True

    if destination_inside or source_inside:
        raise _mother_error(
            operation,
            "MOTHER_INPUT_OVERLAPPING_STORAGE_ROOTS",
            "source and destination object-store trees overlap",
            retry_class="never",
        )


def copy_verified_closure(
    source_root: str | os.PathLike[str],
    destination_root: str | os.PathLike[str],
    *,
    operation: OperationIdentity,
    roots: Iterable[ContentHash],
    references: Mapping[ContentHash, Iterable[ContentHash]],
    expected_members: Iterable[ContentHash],
    faultpoints: Any = None,
) -> tuple[ContentHash, ...]:
    """Verify a source closure, copy exact objects, then verify destination."""

    op = _operation(operation)
    source = _root(source_root, create=False, operation=op)
    destination = _root(destination_root, create=False, operation=op)
    _validate_distinct_roots(source, destination, op)

    root_refs = _references_tuple(roots, "roots", op)
    expected = _references_tuple(expected_members, "expected_members", op)
    graph = _normalized_graph(references, op)

    verified = verify_closure(
        source,
        operation=op,
        roots=root_refs,
        references=graph,
        expected_members=expected,
    )

    _root(destination, create=True, operation=op)
    for reference in verified:
        data = get_verified(source, reference, operation=op)
        copied_reference = put_immutable(
            destination,
            data,
            operation=op,
            faultpoints=faultpoints,
        )
        if copied_reference != reference:
            raise _invalid_closure(
                op,
                "copied object produced a substituted destination identity",
            )

    return verify_closure(
        destination,
        operation=op,
        roots=root_refs,
        references=graph,
        expected_members=expected,
    )


__all__ = [
    "copy_verified_closure",
    "get_verified",
    "put_immutable",
    "verify_closure",
]
