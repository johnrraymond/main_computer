from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


EXPECTED_KIND = "main_computer_multisession_key_request"
EXPECTED_PURPOSE = "request_multi_session_key"

_SECP256K1_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_SECP256K1_GX = 55066263022277343669578718895168534326250603453777594175500187360389116729240
_SECP256K1_GY = 32670510020758816978083085130507043184471273380659243275938904335757337482424
_SECP256K1_G = (_SECP256K1_GX, _SECP256K1_GY)


def die(message: str) -> None:
    raise ValueError(message)


def normalize_address(value: Any) -> str:
    address = str(value or "").strip().lower()
    if not address.startswith("0x") or len(address) != 42:
        die(f"bad address: {value!r}")
    try:
        int(address[2:], 16)
    except ValueError as exc:
        raise ValueError(f"bad address: {value!r}") from exc
    return address


def normalize_chain_id(value: Any) -> str:
    return str(value or "").strip().lower()


def unwrap_blob(record: dict[str, Any]) -> dict[str, Any]:
    if isinstance(record.get("blob"), dict):
        return record["blob"]
    return record


def decode_hex_text(hex_value: str) -> str:
    value = str(hex_value or "")
    if not value.startswith("0x"):
        die("message_hex does not start with 0x")

    try:
        return bytes.fromhex(value[2:]).decode("utf-8")
    except Exception as exc:
        raise ValueError(f"message_hex is not valid utf-8 hex: {exc}") from exc


def parse_iso_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"bad issued_at/expires_at timestamp {value!r}: {exc}") from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def _rotl64(value: int, shift: int) -> int:
    shift %= 64
    return ((value << shift) | (value >> (64 - shift))) & 0xFFFFFFFFFFFFFFFF


_KECCAK_ROUNDS = [
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
]

_KECCAK_ROTATIONS = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]


def _keccak_f1600(state: list[int]) -> None:
    for round_constant in _KECCAK_ROUNDS:
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]

        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl64(state[x + 5 * y], _KECCAK_ROTATIONS[x][y])

        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = b[x + 5 * y] ^ ((~b[((x + 1) % 5) + 5 * y]) & b[((x + 2) % 5) + 5 * y])
                state[x + 5 * y] &= 0xFFFFFFFFFFFFFFFF

        state[0] ^= round_constant


def keccak256(data: bytes) -> bytes:
    """Return Ethereum-compatible Keccak-256, not NIST SHA3-256."""

    rate = 136
    state = [0] * 25

    offset = 0
    while offset + rate <= len(data):
        block = data[offset : offset + rate]
        for lane in range(rate // 8):
            state[lane] ^= int.from_bytes(block[lane * 8 : lane * 8 + 8], "little")
        _keccak_f1600(state)
        offset += rate

    block = bytearray(rate)
    remainder = data[offset:]
    block[: len(remainder)] = remainder
    block[len(remainder)] ^= 0x01
    block[-1] ^= 0x80
    for lane in range(rate // 8):
        state[lane] ^= int.from_bytes(block[lane * 8 : lane * 8 + 8], "little")
    _keccak_f1600(state)

    output = bytearray()
    while len(output) < 32:
        for lane in range(rate // 8):
            output.extend(state[lane].to_bytes(8, "little"))
        if len(output) < 32:
            _keccak_f1600(state)
    return bytes(output[:32])


def personal_sign_message_hash(message_text: str) -> bytes:
    message = str(message_text or "").encode("utf-8")
    prefix = f"\x19Ethereum Signed Message:\n{len(message)}".encode("ascii")
    return keccak256(prefix + message)


def ethereum_address_from_public_key_xy(x: int, y: int) -> str:
    encoded = int(x).to_bytes(32, "big") + int(y).to_bytes(32, "big")
    return "0x" + keccak256(encoded)[-20:].hex()


def _inverse(value: int, modulo: int) -> int:
    return pow(value, -1, modulo)


def _point_add(
    left: tuple[int, int] | None,
    right: tuple[int, int] | None,
) -> tuple[int, int] | None:
    if left is None:
        return right
    if right is None:
        return left

    x1, y1 = left
    x2, y2 = right

    if x1 == x2 and (y1 + y2) % _SECP256K1_P == 0:
        return None

    if left == right:
        slope = (3 * x1 * x1) * _inverse(2 * y1 % _SECP256K1_P, _SECP256K1_P)
    else:
        slope = (y2 - y1) * _inverse((x2 - x1) % _SECP256K1_P, _SECP256K1_P)
    slope %= _SECP256K1_P

    x3 = (slope * slope - x1 - x2) % _SECP256K1_P
    y3 = (slope * (x1 - x3) - y1) % _SECP256K1_P
    return x3, y3


def _point_multiply(scalar: int, point: tuple[int, int] | None) -> tuple[int, int] | None:
    if point is None or scalar % _SECP256K1_N == 0:
        return None

    result: tuple[int, int] | None = None
    addend = point
    value = scalar % _SECP256K1_N

    while value:
        if value & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        value >>= 1

    return result


def _decompress_public_key_x(x: int, odd_y: bool) -> tuple[int, int]:
    if x >= _SECP256K1_P:
        die("signature recovery x coordinate is out of range")
    alpha = (pow(x, 3, _SECP256K1_P) + 7) % _SECP256K1_P
    beta = pow(alpha, (_SECP256K1_P + 1) // 4, _SECP256K1_P)
    if bool(beta & 1) != bool(odd_y):
        beta = _SECP256K1_P - beta
    return x, beta


def _parse_signature(signature: str) -> tuple[int, int, int]:
    text = str(signature or "").strip()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) != 130:
        die("signature must be 65 bytes")
    try:
        raw = bytes.fromhex(text)
    except ValueError as exc:
        raise ValueError("signature is not valid hex") from exc

    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:64], "big")
    v = raw[64]

    if not (1 <= r < _SECP256K1_N):
        die("signature r is out of range")
    if not (1 <= s < _SECP256K1_N):
        die("signature s is out of range")

    if v in {27, 28}:
        recovery_id = v - 27
    elif v in {0, 1, 2, 3}:
        recovery_id = v
    elif v >= 35:
        recovery_id = (v - 35) % 2
    else:
        die(f"unsupported signature recovery id: {v}")
    return r, s, recovery_id


def recover_personal_sign_address(message_text: str, signature: str) -> str:
    r, s, recovery_id = _parse_signature(signature)
    message_hash = personal_sign_message_hash(message_text)
    e = int.from_bytes(message_hash, "big")

    x = r + (recovery_id // 2) * _SECP256K1_N
    r_point = _decompress_public_key_x(x, bool(recovery_id & 1))

    r_inverse = _inverse(r, _SECP256K1_N)
    u1 = (-e * r_inverse) % _SECP256K1_N
    u2 = (s * r_inverse) % _SECP256K1_N
    public_key = _point_add(_point_multiply(u1, _SECP256K1_G), _point_multiply(u2, r_point))
    if public_key is None:
        die("could not recover public key from signature")

    if not _ecdsa_verify(public_key, message_hash, r, s):
        die("signature recovery did not verify")

    return ethereum_address_from_public_key_xy(*public_key)


def _ecdsa_verify(public_key: tuple[int, int], message_hash: bytes, r: int, s: int) -> bool:
    if not (1 <= r < _SECP256K1_N and 1 <= s < _SECP256K1_N):
        return False
    e = int.from_bytes(message_hash, "big")
    w = _inverse(s, _SECP256K1_N)
    u1 = (e * w) % _SECP256K1_N
    u2 = (r * w) % _SECP256K1_N
    point = _point_add(_point_multiply(u1, _SECP256K1_G), _point_multiply(u2, public_key))
    if point is None:
        return False
    return point[0] % _SECP256K1_N == r


def verify_personal_sign_blob(
    blob: dict[str, Any],
    *,
    expected_chain_id: str | None = None,
    max_age_minutes: int | None = None,
) -> dict[str, Any]:
    if blob.get("kind") != EXPECTED_KIND:
        die(f"bad kind: {blob.get('kind')!r}")

    if blob.get("signing_method") != "personal_sign":
        die(f"unsupported signing_method: {blob.get('signing_method')!r}")

    wallet_address = normalize_address(blob.get("wallet_address"))
    blob_chain_id = normalize_chain_id(blob.get("chain_id"))

    if expected_chain_id and blob_chain_id != normalize_chain_id(expected_chain_id):
        die(f"wrong blob chain_id: got {blob_chain_id}, expected {expected_chain_id}")

    signature = str(blob.get("signature") or "").strip()
    if not signature.startswith("0x"):
        die("missing/bad signature")

    message_text = blob.get("message_text")
    if not isinstance(message_text, str) or not message_text.strip():
        die("missing message_text")

    message_hex = blob.get("message_hex")
    if message_hex:
        decoded_text = decode_hex_text(message_hex)
        if decoded_text != message_text:
            die("message_hex does not decode exactly to message_text")

    try:
        message = json.loads(message_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"message_text is not JSON: {exc}") from exc

    if not isinstance(message, dict):
        die("message_text JSON is not an object")

    if blob.get("message") is not None and blob["message"] != message:
        die("blob.message does not exactly match JSON parsed from message_text")

    if message.get("purpose") != EXPECTED_PURPOSE:
        die(f"bad message purpose: {message.get('purpose')!r}")

    message_wallet = normalize_address(message.get("wallet_address"))
    if message_wallet != wallet_address:
        die(f"message wallet mismatch: {message_wallet} != {wallet_address}")

    message_chain_id = normalize_chain_id(message.get("chain_id"))
    if message_chain_id != blob_chain_id:
        die(f"message chain mismatch: {message_chain_id} != {blob_chain_id}")

    if expected_chain_id and message_chain_id != normalize_chain_id(expected_chain_id):
        die(f"wrong message chain_id: got {message_chain_id}, expected {expected_chain_id}")

    if "request_id" not in message or not str(message["request_id"]).strip():
        die("message missing request_id")

    if message.get("issued_at"):
        issued_at = parse_iso_datetime(message["issued_at"])
        if max_age_minutes is not None:
            now = datetime.now(timezone.utc)
            age_seconds = (now - issued_at).total_seconds()
            if age_seconds < -300:
                die(f"issued_at is too far in the future: {message['issued_at']}")
            if age_seconds > max_age_minutes * 60:
                die(
                    f"signed request is too old: age_seconds={int(age_seconds)}, "
                    f"max_age_minutes={max_age_minutes}"
                )

    if message.get("expires_at"):
        expires_at = parse_iso_datetime(message["expires_at"])
        if expires_at < datetime.now(timezone.utc):
            die(f"signed request is expired: {message['expires_at']}")

    recovered = recover_personal_sign_address(message_text, signature)

    if recovered != wallet_address:
        die(f"signature recovered {recovered}, expected {wallet_address}")

    return {
        "ok": True,
        "wallet_address": wallet_address,
        "recovered_address": recovered,
        "matched": True,
        "chain_id": blob_chain_id,
        "request_id": str(message.get("request_id")),
        "issued_at": message.get("issued_at"),
        "expires_at": message.get("expires_at"),
        "origin": message.get("origin"),
        "signature": signature,
        "message": message,
    }
