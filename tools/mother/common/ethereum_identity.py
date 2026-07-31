"""Small, dependency-light Ethereum identity primitives for Mother private state."""

from __future__ import annotations

import re
import secrets


_SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_MASK64 = 0xFFFFFFFFFFFFFFFF
_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
_ROTATION_OFFSETS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def is_private_key(value: object) -> bool:
    if type(value) is not str or not _PRIVATE_KEY_RE.fullmatch(value):
        return False
    scalar = int(value[2:], 16)
    return 0 < scalar < _SECP256K1_ORDER


def is_address(value: object) -> bool:
    return type(value) is str and _ADDRESS_RE.fullmatch(value) is not None


def _rotate_left_64(value: int, shift: int) -> int:
    shift %= 64
    if shift == 0:
        return value & _MASK64
    return ((value << shift) | (value >> (64 - shift))) & _MASK64


def _keccakf1600(state: list[int]) -> None:
    for round_constant in _ROUND_CONSTANTS:
        column = [
            state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
            for x in range(5)
        ]
        for x in range(5):
            delta = column[(x - 1) % 5] ^ _rotate_left_64(column[(x + 1) % 5], 1)
            for y in range(5):
                state[x + 5 * y] ^= delta

        moved = [0] * 25
        for x in range(5):
            for y in range(5):
                moved[y + 5 * ((2 * x + 3 * y) % 5)] = _rotate_left_64(
                    state[x + 5 * y], _ROTATION_OFFSETS[x][y]
                )

        for y in range(5):
            row = [moved[x + 5 * y] for x in range(5)]
            for x in range(5):
                state[x + 5 * y] = (
                    row[x]
                    ^ ((~row[(x + 1) % 5]) & row[(x + 2) % 5] & _MASK64)
                )
        state[0] ^= round_constant


def keccak256(data: bytes) -> bytes:
    if type(data) is not bytes:
        raise TypeError("data must be exact bytes")
    rate_bytes = 136
    state = [0] * 25
    offset = 0
    while offset + rate_bytes <= len(data):
        block = data[offset : offset + rate_bytes]
        for index in range(rate_bytes // 8):
            state[index] ^= int.from_bytes(
                block[index * 8 : (index + 1) * 8], "little"
            )
        _keccakf1600(state)
        offset += rate_bytes

    tail = bytearray(data[offset:])
    tail.append(0x01)
    tail.extend(b"\x00" * (rate_bytes - len(tail)))
    tail[-1] ^= 0x80
    for index in range(rate_bytes // 8):
        state[index] ^= int.from_bytes(
            tail[index * 8 : (index + 1) * 8], "little"
        )
    _keccakf1600(state)

    output = bytearray()
    while len(output) < 32:
        for index in range(rate_bytes // 8):
            output.extend(state[index].to_bytes(8, "little"))
            if len(output) >= 32:
                break
        if len(output) < 32:
            _keccakf1600(state)
    return bytes(output[:32])


def checksum_address(value: str) -> str:
    raw = value.lower().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{40}", raw):
        raise ValueError("address must contain exactly 20 hexadecimal bytes")
    digest = keccak256(raw.encode("ascii")).hex()
    return "0x" + "".join(
        character.upper() if int(digest[index], 16) >= 8 else character
        for index, character in enumerate(raw)
    )


def private_key_to_address(private_key: str) -> str:
    if not is_private_key(private_key):
        raise ValueError("private key must be a valid 32-byte secp256k1 scalar")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ValueError("cryptography is required for Ethereum key derivation") from exc

    secret = int(private_key[2:], 16)
    key = ec.derive_private_key(secret, ec.SECP256K1())
    public_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return checksum_address(keccak256(public_bytes[1:])[-20:].hex())


def generate_private_key() -> str:
    while True:
        raw = secrets.token_bytes(32)
        scalar = int.from_bytes(raw, "big")
        if 0 < scalar < _SECP256K1_ORDER:
            return "0x" + raw.hex()


def validate_identity(identity: object, *, path: str) -> tuple[str, str]:
    if type(identity) is not dict or set(identity) != {"address", "private_key"}:
        raise ValueError(f"{path} must contain exactly address and private_key")
    private_key = identity.get("private_key")
    address = identity.get("address")
    if not is_private_key(private_key):
        raise ValueError(f"{path}.private_key is invalid")
    derived = private_key_to_address(private_key)
    if not is_address(address) or str(address).lower() != derived.lower():
        raise ValueError(f"{path}.address does not match its private_key")
    return derived, private_key


__all__ = [
    "checksum_address",
    "generate_private_key",
    "is_address",
    "is_private_key",
    "keccak256",
    "private_key_to_address",
    "validate_identity",
]
