"""Hash-addressed immutable object storage and verified closure copying."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import os
from pathlib import Path
from typing import Any

from . import atomic_files, hashing
from .errors import MotherError
from .models import ContentHash


_MODULE_ID = "MOTHER-OFM-CORE-012"
_OPERATION_ID = "MOTHER-OP-INTERNAL"


def _mother_error(
    code: str,
    message: str,
    *,
    retry_class: str,
    authority_effect: str = "none",
) -> MotherError:
    return MotherError(
        code=code,
        message=message,
        operation_id=_OPERATION_ID,
        module_id=_MODULE_ID,
        retry_class=retry_class,
        authority_effect=authority_effect,
    )


def _as_path(value: str | os.PathLike[str], name: str) -> Path:
    if isinstance(value, str):
        if "\x00" in value:
            raise _mother_error(
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


def _validate_no_symlink(path: Path) -> None:
    unsafe = _first_symlink(path)
    if unsafe is not None:
        raise _mother_error(
            "MOTHER_INPUT_UNSAFE_PATH",
            f"unsafe symlink path component: {unsafe}",
            retry_class="never",
        )


def _root(value: str | os.PathLike[str], *, create: bool) -> Path:
    root = _as_path(value, "object-store root")
    _validate_no_symlink(root)
    if create:
        root.mkdir(parents=True, exist_ok=True)
        _validate_no_symlink(root)
    return root


def _content_hash(value: ContentHash, name: str = "reference") -> ContentHash:
    if not isinstance(value, ContentHash):
        raise TypeError(f"{name} must be ContentHash")
    # Reconstruct at the boundary to reject malformed instances/subclasses.
    return ContentHash(algorithm=value.algorithm, digest=value.digest)


def _object_path(root: Path, reference: ContentHash) -> Path:
    path = root / reference.algorithm / reference.digest[:2] / reference.digest
    _validate_no_symlink(path)
    return path


def _payload(value: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes-like")
    return bytes(value)


def _object_corrupt(message: str) -> MotherError:
    return _mother_error(
        "MOTHER_STATE_OBJECT_CORRUPT",
        message,
        retry_class="never",
    )


def put_immutable(
    root: str | os.PathLike[str],
    data: bytes | bytearray | memoryview,
    *,
    faultpoints: Any = None,
) -> ContentHash:
    """Publish exact bytes at their SHA-256 address, idempotently."""

    payload = _payload(data)
    reference = hashing.sha256(payload)
    store_root = _root(root, create=True)
    path = _object_path(store_root, reference)

    if path.exists():
        _validate_no_symlink(path)
        if not path.is_file():
            raise _mother_error(
                "MOTHER_INPUT_UNSAFE_PATH",
                "content-addressed object path is not a regular file",
                retry_class="never",
            )
        existing = path.read_bytes()
        if existing != payload:
            raise _object_corrupt(
                "existing content-addressed path contains different exact bytes"
            )
        return reference

    try:
        atomic_files.durable_create(path, payload, faultpoints=faultpoints)
    except MotherError as exc:
        if exc.code != "MOTHER_CONFLICT_DURABLE_TARGET_EXISTS":
            raise
        # A concurrent writer may have published the same immutable object.
        _validate_no_symlink(path)
        try:
            existing = path.read_bytes()
        except FileNotFoundError:
            raise exc
        if existing != payload:
            raise _object_corrupt(
                "competing publication placed different bytes at one content address"
            ) from exc
    return reference


def get_verified(
    root: str | os.PathLike[str],
    reference: ContentHash,
) -> bytes:
    """Read exact object bytes and verify they still hash to their address."""

    ref = _content_hash(reference)
    store_root = _root(root, create=False)
    path = _object_path(store_root, ref)
    if not path.exists():
        raise _mother_error(
            "MOTHER_STATE_OBJECT_MISSING",
            "requested immutable object is absent",
            retry_class="after-reobserve",
        )
    _validate_no_symlink(path)
    if not path.is_file():
        raise _mother_error(
            "MOTHER_INPUT_UNSAFE_PATH",
            "content-addressed object path is not a regular file",
            retry_class="never",
        )

    data = path.read_bytes()
    actual = hashing.sha256(data)
    if actual != ref:
        raise _object_corrupt(
            "content-addressed object bytes do not match their declared hash"
        )
    return data


def _references_tuple(
    values: Iterable[ContentHash],
    name: str,
) -> tuple[ContentHash, ...]:
    if isinstance(
        values,
        (str, bytes, bytearray, memoryview, Mapping, set, frozenset),
    ):
        raise TypeError(f"{name} must be an ordered iterable of ContentHash values")
    try:
        result = tuple(_content_hash(value, name) for value in values)
    except TypeError:
        raise
    if len(set(result)) != len(result):
        raise _mother_error(
            "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER",
            f"{name} contains duplicate content identities",
            retry_class="never",
        )
    return result


def _normalized_graph(
    references: Mapping[ContentHash, Iterable[ContentHash]],
) -> dict[ContentHash, tuple[ContentHash, ...]]:
    if not isinstance(references, Mapping):
        raise TypeError("references must be a mapping")
    graph: dict[ContentHash, tuple[ContentHash, ...]] = {}
    for raw_parent, raw_children in references.items():
        parent = _content_hash(raw_parent, "reference graph key")
        if parent in graph:
            raise _mother_error(
                "MOTHER_SCHEMA_DUPLICATE_CLOSURE_MEMBER",
                "reference graph contains duplicate parent identities",
                retry_class="never",
            )
        graph[parent] = _references_tuple(
            raw_children,
            f"children of {parent.digest}",
        )
    return graph


def _invalid_closure(message: str) -> MotherError:
    return _mother_error(
        "MOTHER_RECOVERY_INVALID_CLOSURE",
        message,
        retry_class="never",
    )


def _closure_order(
    roots: tuple[ContentHash, ...],
    graph: Mapping[ContentHash, tuple[ContentHash, ...]],
) -> tuple[ContentHash, ...]:
    order: list[ContentHash] = []
    state: dict[ContentHash, int] = {}

    def visit(node: ContentHash) -> None:
        status = state.get(node, 0)
        if status == 1:
            raise _invalid_closure("closure graph contains a cycle")
        if status == 2:
            return
        children = graph.get(node)
        if children is None:
            raise _invalid_closure("closure graph is missing a referenced row")
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
    roots: Iterable[ContentHash],
    references: Mapping[ContentHash, Iterable[ContentHash]],
    expected_members: Iterable[ContentHash],
) -> tuple[ContentHash, ...]:
    """Verify an exact, acyclic, fully reachable transitive object closure."""

    store_root = _root(root, create=False)
    root_refs = _references_tuple(roots, "roots")
    expected = _references_tuple(expected_members, "expected_members")
    graph = _normalized_graph(references)

    if not root_refs:
        if expected or graph:
            raise _invalid_closure("empty roots do not describe the supplied closure")
        return ()

    order = _closure_order(root_refs, graph)
    reachable = set(order)
    if reachable != set(expected):
        raise _invalid_closure(
            "reachable closure members do not equal the exact expected member set"
        )
    if reachable != set(graph):
        raise _invalid_closure(
            "reference graph contains unreachable or omitted rows"
        )

    for reference in order:
        try:
            get_verified(store_root, reference)
        except MotherError as exc:
            if exc.code == "MOTHER_INPUT_UNSAFE_PATH":
                raise
            if exc.code in {
                "MOTHER_STATE_OBJECT_MISSING",
                "MOTHER_STATE_OBJECT_CORRUPT",
            }:
                raise _invalid_closure(
                    "closure contains a missing or corrupt immutable object"
                ) from exc
            raise
    return order


def _validate_distinct_roots(source: Path, destination: Path) -> None:
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
            "MOTHER_INPUT_OVERLAPPING_STORAGE_ROOTS",
            "source and destination object-store trees overlap",
            retry_class="never",
        )


def copy_verified_closure(
    source_root: str | os.PathLike[str],
    destination_root: str | os.PathLike[str],
    *,
    roots: Iterable[ContentHash],
    references: Mapping[ContentHash, Iterable[ContentHash]],
    expected_members: Iterable[ContentHash],
    faultpoints: Any = None,
) -> tuple[ContentHash, ...]:
    """Verify a source closure, copy exact objects, then verify destination."""

    source = _root(source_root, create=False)
    destination = _root(destination_root, create=False)
    _validate_distinct_roots(source, destination)

    # Normalize once so generators cannot yield different contracts between
    # source verification, copying, and destination verification.
    root_refs = _references_tuple(roots, "roots")
    expected = _references_tuple(expected_members, "expected_members")
    graph = _normalized_graph(references)

    verified = verify_closure(
        source,
        roots=root_refs,
        references=graph,
        expected_members=expected,
    )

    _root(destination, create=True)
    for reference in verified:
        data = get_verified(source, reference)
        copied_reference = put_immutable(
            destination,
            data,
            faultpoints=faultpoints,
        )
        if copied_reference != reference:
            raise _invalid_closure(
                "copied object produced a substituted destination identity"
            )

    return verify_closure(
        destination,
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
