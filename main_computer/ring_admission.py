from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


RING_ADMISSION_DEFAULT_MINIMUM_ALLOWED_RING = 3
RING_ADMISSION_SUPPORTED_RINGS = {0, 1, 2, 3}


def normalize_wallet_address(value: Any) -> str:
    text = str(value or "").strip()
    return text.lower()


def normalize_requested_ring(value: Any, *, default: int | None = None, field_name: str = "requested_ring") -> int:
    if value is None or value == "":
        if default is None:
            raise ValueError(f"{field_name} is required.")
        return int(default)
    try:
        ring = int(str(value).strip(), 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer ring.") from exc
    if ring not in RING_ADMISSION_SUPPORTED_RINGS:
        raise ValueError(f"{field_name} must be one of 0, 1, 2, or 3.")
    return ring


@dataclass(frozen=True)
class RingAdmissionDecision:
    ok: bool
    requested_ring: int
    effective_ring: int | None
    minimum_allowed_ring: int
    fallback_ring: int | None
    status: str
    message: str
    error: str | None = None

    @property
    def allowed_min_ring(self) -> int:
        return self.minimum_allowed_ring

    def as_response_fields(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "requested_ring": self.requested_ring,
            "effective_ring": self.effective_ring,
            "minimum_allowed_ring": self.minimum_allowed_ring,
            "allowed_min_ring": self.minimum_allowed_ring,
            "ring_admission_status": self.status,
            "ring_admission_message": self.message,
            "message": self.message,
        }
        if self.fallback_ring is not None:
            payload["fallback_ring"] = self.fallback_ring
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class RingAdmissionConfig:
    default_min_ring: int = RING_ADMISSION_DEFAULT_MINIMUM_ALLOWED_RING
    wallet_min_ring: dict[str, int] = field(default_factory=dict)
    source_path: str = ""
    explicit_path: bool = False
    load_ok: bool = True
    load_error: str = ""
    force_default_ring: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, source_path: str = "", explicit_path: bool = False) -> "RingAdmissionConfig":
        default_min_ring = normalize_requested_ring(
            payload.get("default_min_ring", RING_ADMISSION_DEFAULT_MINIMUM_ALLOWED_RING),
            default=RING_ADMISSION_DEFAULT_MINIMUM_ALLOWED_RING,
            field_name="default_min_ring",
        )
        raw_wallets = payload.get("wallet_min_ring", {})
        if raw_wallets is None:
            raw_wallets = {}
        if not isinstance(raw_wallets, dict):
            raise ValueError("wallet_min_ring must be an object mapping wallet addresses to minimum rings.")
        wallet_min_ring: dict[str, int] = {}
        for raw_wallet, raw_ring in raw_wallets.items():
            wallet = normalize_wallet_address(raw_wallet)
            if not wallet:
                continue
            wallet_min_ring[wallet] = normalize_requested_ring(
                raw_ring,
                default=default_min_ring,
                field_name=f"wallet_min_ring[{wallet}]",
            )
        return cls(
            default_min_ring=default_min_ring,
            wallet_min_ring=wallet_min_ring,
            source_path=str(source_path or ""),
            explicit_path=bool(explicit_path),
            load_ok=True,
            load_error="",
            force_default_ring=False,
        )

    @classmethod
    def default(cls) -> "RingAdmissionConfig":
        return cls(force_default_ring=True)

    def minimum_allowed_ring_for_wallet(self, wallet_address: Any) -> int:
        wallet = normalize_wallet_address(wallet_address)
        return int(self.wallet_min_ring.get(wallet, self.default_min_ring))

    def evaluate(self, *, wallet_address: Any, requested_ring: Any) -> RingAdmissionDecision:
        requested = normalize_requested_ring(requested_ring, default=self.default_min_ring)
        minimum_allowed_ring = self.minimum_allowed_ring_for_wallet(wallet_address)
        if self.force_default_ring:
            default_ring = int(self.default_min_ring)
            return RingAdmissionDecision(
                ok=True,
                requested_ring=default_ring,
                effective_ring=default_ring,
                minimum_allowed_ring=default_ring,
                fallback_ring=None,
                status="accepted",
                message=f"No ring admission config is configured; defaulting worker to ring {default_ring}.",
            )
        if requested < minimum_allowed_ring:
            return RingAdmissionDecision(
                ok=False,
                requested_ring=requested,
                effective_ring=None,
                minimum_allowed_ring=minimum_allowed_ring,
                fallback_ring=minimum_allowed_ring,
                status="rejected",
                error="ring_not_allowed",
                message=(
                    f"Wallet is not authorized for ring {requested}; retry at ring {minimum_allowed_ring}. "
                    f"This wallet may only register at ring {minimum_allowed_ring} or lower-trust rings."
                ),
            )
        return RingAdmissionDecision(
            ok=True,
            requested_ring=requested,
            effective_ring=requested,
            minimum_allowed_ring=minimum_allowed_ring,
            fallback_ring=None,
            status="accepted",
            message=f"Wallet is authorized for ring {requested}.",
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "default_min_ring": int(self.default_min_ring),
            "wallet_min_ring": {
                str(wallet).lower(): int(ring)
                for wallet, ring in sorted(self.wallet_min_ring.items())
            },
        }

    def config_hash(self) -> str:
        raw = json.dumps(self.canonical_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    def public_status(self) -> dict[str, Any]:
        ring0_count = sum(1 for ring in self.wallet_min_ring.values() if int(ring) == 0)
        return {
            "ring_config_enabled": True,
            "ring_config_path": self.source_path or None,
            "ring_config_explicit_path": bool(self.explicit_path),
            "ring_config_load_ok": bool(self.load_ok),
            "ring_config_force_default_ring": bool(self.force_default_ring),
            "ring_config_default_min_ring": int(self.default_min_ring),
            "ring_config_allowlisted_wallet_count": len(self.wallet_min_ring),
            "ring_config_allowlisted_ring0_wallet_count": ring0_count,
            "ring_config_hash": self.config_hash(),
        }


def load_ring_admission_config(path: str | Path | None) -> RingAdmissionConfig:
    if path is None or not str(path).strip():
        return RingAdmissionConfig.default()
    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not load ring admission config {config_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Ring admission config {config_path} must be a JSON object.")
    try:
        return RingAdmissionConfig.from_payload(payload, source_path=str(config_path), explicit_path=True)
    except Exception as exc:
        raise RuntimeError(f"Invalid ring admission config {config_path}: {exc}") from exc
