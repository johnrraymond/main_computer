"""Main Computer Authenticator v1: portable P-256 public-key proofs, no CA.

A signed object is canonical UTF-8 JSON: sort_keys, compact separators, ensure_ascii.
Do not reuse this key as a marketplace wallet signing key.
"""
from __future__ import annotations
import base64
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def canonical_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('utf-8')


def b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def generate_identity():
    """Test/client helper. The server NEVER creates device private keys."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii')
    return key, public


def public_key_fingerprint(public_pem: str) -> str:
    key = load_public_key(public_pem)
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


def load_public_key(public_pem: str):
    if len(public_pem) > 2048:
        raise ValueError('Public key too long')
    try:
        key = serialization.load_pem_public_key(public_pem.encode('ascii'))
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ValueError('Invalid public key') from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError('Expected P-256 ECDSA public key')
    return key


def sign_object(private_key, value: dict) -> str:
    return b64encode(private_key.sign(canonical_bytes(value), ec.ECDSA(hashes.SHA256())))


def verify_object(public_pem: str, value: dict, signature: str) -> bool:
    try:
        signature_raw = b64decode(signature)
        if len(signature_raw) > 128:
            return False
        load_public_key(public_pem).verify(signature_raw, canonical_bytes(value), ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError, UnicodeError, base64.binascii.Error):
        return False
