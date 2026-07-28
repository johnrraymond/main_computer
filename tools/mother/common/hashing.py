"""SHA-256 content hashes and domain-separated aggregate roots."""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
from pathlib import Path

from .models import ContentHash


_ORDERED_DOMAIN = b"main_computer.mother.ordered_root.v1\x00"
_SET_DOMAIN = b"main_computer.mother.set_root.v1\x00"
_MEMBER_DOMAIN = b"sha256\x00"


def sha256(data: bytes | bytearray | memoryview) -> ContentHash:
    """Return the declared immutable SHA-256 content-hash model."""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("sha256 requires bytes-like input")
    return ContentHash(
        algorithm="sha256",
        digest=hashlib.sha256(bytes(data)).hexdigest(),
    )


def hash_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> ContentHash:
    """Hash the exact bytes of one regular file."""

    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return ContentHash(algorithm="sha256", digest=digest.hexdigest())


def _validated_members(values: Iterable[ContentHash]) -> tuple[ContentHash, ...]:
    members = tuple(values)
    for member in members:
        if not isinstance(member, ContentHash):
            raise TypeError("root members must be ContentHash values")
        # Reconstruct to reject forged or mutated subclasses at this boundary.
        ContentHash(algorithm=member.algorithm, digest=member.digest)
    return members


def _aggregate(domain: bytes, members: tuple[ContentHash, ...]) -> ContentHash:
    digest = hashlib.sha256()
    digest.update(domain)
    digest.update(len(members).to_bytes(8, "big"))
    for member in members:
        member_bytes = bytes.fromhex(member.digest)
        digest.update(_MEMBER_DOMAIN)
        digest.update(len(member_bytes).to_bytes(4, "big"))
        digest.update(member_bytes)
    return ContentHash(algorithm="sha256", digest=digest.hexdigest())


def ordered_root(values: Iterable[ContentHash]) -> ContentHash:
    """Hash an ordered sequence; reordering members changes the result."""

    return _aggregate(_ORDERED_DOMAIN, _validated_members(values))


def set_root(values: Iterable[ContentHash]) -> ContentHash:
    """Hash a mathematical set in canonical digest order."""

    members = _validated_members(values)
    identities = tuple((member.algorithm, member.digest) for member in members)
    if len(set(identities)) != len(identities):
        raise ValueError("set_root rejects duplicate members")
    canonical = tuple(
        sorted(members, key=lambda member: (member.algorithm, member.digest))
    )
    return _aggregate(_SET_DOMAIN, canonical)


__all__ = ["ContentHash", "hash_file", "ordered_root", "set_root", "sha256"]
