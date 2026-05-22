from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
from dataclasses import dataclass
from typing import Any


# RFC 3526, 2048-bit MODP Group 14 prime. This keeps the hub protocol free of
# third-party dependencies while giving the requester and worker temporary public
# keys for a hub-blind envelope. Production deployments should swap this module
# for an audited cryptography backend and authenticated peer identities.
_DH_P_HEX = """
FFFFFFFF FFFFFFFF C90FDAA2 2168C234 C4C6628B 80DC1CD1
29024E08 8A67CC74 020BBEA6 3B139B22 514A0879 8E3404DD
EF9519B3 CD3A431B 302B0A6D F25F1437 4FE1356D 6D51C245
E485B576 625E7EC6 F44C42E9 A637ED6B 0BFF5CB6 F406B7ED
EE386BFB 5A899FA5 AE9F2411 7C4B1FE6 49286651 ECE45B3D
C2007CB8 A163BF05 98DA4836 1C55D39A 69163FA8 FD24CF5F
83655D23 DCA3AD96 1C62F356 208552BB 9ED52907 7096966D
670C354E 4ABC9804 F1746C08 CA18217C 32905E46 2E36CE3B
E39E772C 180E8603 9B2783A2 EC07A28F B5C55DF0 6F4C52C9
DE2BCBF6 95581718 3995497C EA956AE5 15D22618 98FA0510
15728E5A 8AACAA68 FFFFFFFF FFFFFFFF
"""
_DH_P = int("".join(_DH_P_HEX.split()), 16)
_DH_G = 2
_DH_BYTES = (_DH_P.bit_length() + 7) // 8

HUB_SECURITY_PROFILE = "mc-hub-e2ee-v1-dh-hmac-stream"


_LOCAL_DEV_DOCKER_HOSTS = {"host.docker.internal", "gateway.docker.internal"}


def _is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower().rstrip(".")
    if not normalized:
        return False
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_explicit_local_dev_network_host(host: str) -> bool:
    """Return whether an HTTP host is acceptable after an explicit dev-network opt-in.

    High-security hub payloads are encrypted before they cross the hub, but plain
    HTTP still leaks transport metadata.  The default policy therefore permits
    HTTP only for true loopback URLs.  Docker Compose development uses service
    names such as ``hub`` and ``hub-worker`` that are local to the compose
    network, not loopback from inside the app container.  Those names are allowed
    only when the caller has explicitly opted into local development network HTTP.
    """

    normalized = str(host or "").strip().lower().rstrip(".")
    if not normalized:
        return False
    if _is_loopback_host(normalized):
        return True
    if normalized in _LOCAL_DEV_DOCKER_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        # Docker Compose service names are intentionally single-label names.
        # Do not treat dotted public names as local development endpoints.
        return "." not in normalized and ":" not in normalized
    return address.is_private or address.is_link_local or address.is_loopback


def hub_transport_is_encrypted_or_loopback(url: str, *, allow_insecure_dev_network: bool = False) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(str(url or ""))
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme == "https":
        return True
    if scheme != "http":
        return False
    if _is_loopback_host(host):
        return True
    if allow_insecure_dev_network and _is_explicit_local_dev_network_host(host):
        return True
    return False


@dataclass(frozen=True)
class HubSessionKeyPair:
    private_key: int
    public_key: str


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def _int_to_b64(value: int) -> str:
    return _b64e(value.to_bytes(_DH_BYTES, "big"))


def _b64_to_int(value: str) -> int:
    return int.from_bytes(_b64d(value), "big")


def generate_hub_session_keypair() -> HubSessionKeyPair:
    private_key = secrets.randbelow(_DH_P - 3) + 2
    public_value = pow(_DH_G, private_key, _DH_P)
    return HubSessionKeyPair(private_key=private_key, public_key=_int_to_b64(public_value))


def derive_hub_session_key(*, private_key: int, peer_public_key: str, session_id: str) -> bytes:
    peer_value = _b64_to_int(peer_public_key)
    if peer_value <= 1 or peer_value >= _DH_P - 1:
        raise ValueError("Invalid hub session public key.")
    shared = pow(peer_value, private_key, _DH_P).to_bytes(_DH_BYTES, "big")
    salt = hashlib.sha256(("main-computer-hub-session:" + str(session_id)).encode("utf-8")).digest()
    pseudo = hmac.new(salt, shared, hashlib.sha256).digest()
    return hmac.new(pseudo, b"hub-e2ee-v1 envelope key", hashlib.sha256).digest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _xor_keystream(key: bytes, nonce: bytes, size: int) -> bytes:
    chunks: list[bytes] = []
    counter = 0
    while sum(len(chunk) for chunk in chunks) < size:
        chunks.append(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return b"".join(chunks)[:size]


def encrypt_hub_envelope(payload: dict[str, Any], *, key: bytes, aad: dict[str, Any] | None = None) -> dict[str, Any]:
    aad = aad or {}
    nonce = secrets.token_bytes(16)
    plaintext = _canonical_json(payload)
    stream = _xor_keystream(key, nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    tag = hmac.new(key, _canonical_json(aad) + nonce + ciphertext, hashlib.sha256).digest()
    return {
        "profile": HUB_SECURITY_PROFILE,
        "aad": aad,
        "nonce": _b64e(nonce),
        "ciphertext": _b64e(ciphertext),
        "tag": _b64e(tag),
    }


def decrypt_hub_envelope(envelope: dict[str, Any], *, key: bytes, aad: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise ValueError("Encrypted hub envelope must be an object.")
    if envelope.get("profile") != HUB_SECURITY_PROFILE:
        raise ValueError("Unsupported hub envelope profile.")
    expected_aad = aad if aad is not None else envelope.get("aad", {})
    if not isinstance(expected_aad, dict):
        raise ValueError("Hub envelope authenticated data must be an object.")
    nonce = _b64d(str(envelope.get("nonce", "")))
    ciphertext = _b64d(str(envelope.get("ciphertext", "")))
    tag = _b64d(str(envelope.get("tag", "")))
    expected = hmac.new(key, _canonical_json(expected_aad) + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("Hub envelope authentication failed.")
    stream = _xor_keystream(key, nonce, len(ciphertext))
    plaintext = bytes(a ^ b for a, b in zip(ciphertext, stream))
    data = json.loads(plaintext.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Hub envelope plaintext must be an object.")
    return data
