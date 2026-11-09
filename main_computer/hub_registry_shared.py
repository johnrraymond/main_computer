"""Shared registry models, offer/pricing logic and transport checks.

Common to the legacy Hub registry and the FDB-backed Dev-Test registry.
No HTTP server, local registry files, FoundationDB client, requester transport,
or Worker supervisor is imported here. Changes to economics are out of scope.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from dataclasses import asdict, dataclass, field
from typing import Any

from main_computer.credit_units import (
    credit_decimal_text_to_wei,
    credit_wei_product,
    credit_wei_to_decimal_text,
)
from main_computer.hub_credit_models import DEFAULT_WORKER_PAYOUT_PRECISION_PLACES
from main_computer.hub_security import hub_transport_is_encrypted_or_loopback

HUB_WORKER_STALE_AFTER_SECONDS = 90.0
HUB_WORKER_LEASE_SECONDS = 600.0
HUB_WORKER_INSTANCE_SLOT_LIMIT = 1
PHASE9_PRICING_MODE = "market_offer_fixed_per_call_v0"
PHASE9_PRICING_TYPE = "fixed_per_call_v0"
PHASE9_EXECUTION_MODE = "worker_pull_v0"


def _hub_as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "enable"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "disable"}:
        return False
    return default


def _hub_as_int(value: Any, default: int = 0, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    if minimum is not None:
        number = max(int(minimum), number)
    if maximum is not None:
        number = min(int(maximum), number)
    return number


def _hub_as_decimal(
    value: Any,
    default: str = "0",
    *,
    minimum: str | None = None,
    maximum: str | None = None,
) -> Decimal:
    try:
        number = Decimal(str(value).strip())
        if not number.is_finite():
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        number = Decimal(str(default))
    if minimum is not None:
        number = max(Decimal(str(minimum)), number)
    if maximum is not None:
        number = min(Decimal(str(maximum)), number)
    return number


def _hub_decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _hub_credit_wei_from_decimal_text(
    value: Any,
    default: str = "0",
    *,
    minimum_wei: int | None = None,
    maximum_wei: int | None = None,
) -> int:
    return credit_decimal_text_to_wei(
        value,
        default=default,
        minimum_wei=minimum_wei,
        maximum_wei=maximum_wei,
        round_up=True,
    )


def _hub_credit_wei_from_value(value: Any, default: str = "1", *, minimum_wei: int = 1) -> int:
    return _hub_credit_wei_from_decimal_text(value, default, minimum_wei=minimum_wei)


def _hub_credit_wei_from_explicit_or_decimal(
    explicit_wei: Any,
    decimal_value: Any,
    default: str = "1",
    *,
    minimum_wei: int = 1,
) -> int:
    if explicit_wei not in (None, ""):
        try:
            parsed = int(str(explicit_wei).strip())
            if parsed > 0:
                return max(int(minimum_wei), parsed)
        except (TypeError, ValueError):
            pass
    return _hub_credit_wei_from_value(decimal_value, default, minimum_wei=minimum_wei)


def _hub_credit_public_value_from_wei(credit_wei: Any) -> int | str:
    text = credit_wei_to_decimal_text(credit_wei)
    return int(text) if text.isdigit() else text


def _hub_credit_display_from_wei(credit_wei: Any) -> str:
    return credit_wei_to_decimal_text(credit_wei)


def _hub_normalized_pricing_payload(
    capabilities: dict[str, Any],
    payload: dict[str, Any],
    default: str = "1",
) -> tuple[int, int | str, str, dict[str, Any]]:
    """Normalize human decimal credit fields into integer wei-string pricing.

    UI/API callers may send ETH/credit-style decimal strings such as ``"1.024"``.
    Hub storage and signed-order comparisons must carry the exact integer wei
    value so Python never calls ``int("1.024")`` and browser callers can use
    ``BigInt`` without precision loss.
    """

    pricing_payload = (
        dict(capabilities.get("pricing", {}))
        if isinstance(capabilities.get("pricing"), dict)
        else {}
    )
    credit_price_wei = _hub_pricing_credit_wei(pricing_payload, payload, default)
    credit_price = _hub_credit_public_value_from_wei(credit_price_wei)
    credit_price_display = _hub_credit_display_from_wei(credit_price_wei)
    pricing_payload.update(
        {
            "pricing_type": str(pricing_payload.get("pricing_type") or PHASE9_PRICING_TYPE),
            "credits_per_request": credit_price,
            "credits_per_request_wei": str(credit_price_wei),
            "credits_per_request_display": credit_price_display,
            "estimated_credits_per_request": credit_price,
            "estimated_credits_per_request_wei": str(credit_price_wei),
            "estimated_credits_per_request_display": credit_price_display,
            "unit": str(pricing_payload.get("unit") or "compute_credit"),
        }
    )
    return credit_price_wei, credit_price, credit_price_display, pricing_payload


def _hub_pricing_target_output_tokens(pricing: dict[str, Any], payload: dict[str, Any], default: int = 1024) -> int:
    return _hub_as_int(
        pricing.get(
            "target_output_tokens",
            pricing.get("target_tokens_per_request", payload.get("target_output_tokens", payload.get("target_tokens", default))),
        ),
        default,
        minimum=1,
        maximum=128_000,
    )


def _hub_pricing_credit_per_token_wei(pricing: dict[str, Any], payload: dict[str, Any], default: str = "0.001") -> int:
    raw_wei = pricing.get("credits_per_token_wei", payload.get("credits_per_token_wei"))
    if raw_wei not in (None, ""):
        try:
            parsed = int(str(raw_wei).strip())
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
    return _hub_credit_wei_from_decimal_text(
        pricing.get("credits_per_token", payload.get("credits_per_token", default)),
        default,
        minimum_wei=1,
    )


def _hub_pricing_credit_wei(pricing: dict[str, Any], payload: dict[str, Any], default: str = "1") -> int:
    raw_estimated_wei = pricing.get("estimated_credits_per_request_wei", payload.get("estimated_credits_per_request_wei"))
    if raw_estimated_wei not in (None, ""):
        try:
            parsed = int(str(raw_estimated_wei).strip())
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass

    pricing_type = str(pricing.get("pricing_type") or pricing.get("type") or "").strip().lower()
    token_priced = (
        "token" in pricing_type
        or pricing.get("credits_per_token") not in (None, "")
        or pricing.get("credits_per_token_wei") not in (None, "")
        or payload.get("credits_per_token") not in (None, "")
        or payload.get("credits_per_token_wei") not in (None, "")
    )
    if token_priced:
        token_wei = _hub_pricing_credit_per_token_wei(pricing, payload, "0.001")
        target_output_tokens = _hub_pricing_target_output_tokens(pricing, payload, 1024)
        estimated_wei = credit_wei_product(target_output_tokens, token_wei)
        if estimated_wei > 0:
            return estimated_wei

    raw_wei = pricing.get("credits_per_request_wei", payload.get("credits_per_request_wei"))
    if raw_wei not in (None, ""):
        try:
            parsed = int(str(raw_wei).strip())
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
    return _hub_credit_wei_from_value(
        pricing.get("credits_per_request", payload.get("credits_per_request", default)),
        default,
        minimum_wei=1,
    )


def _hub_ceil_decimal_to_int(value: Decimal, *, minimum: int = 0) -> int:
    return max(int(minimum), int(value.to_integral_value(rounding=ROUND_CEILING)))


def _phase9_offer_id(
    *,
    worker_node_id: str,
    models: list[str],
    credits_per_request_wei: int,
    execution_mode: str,
    pricing_type: str = PHASE9_PRICING_TYPE,
    credits_per_token_wei: int | None = None,
) -> str:
    seed_payload = {
        "worker_node_id": str(worker_node_id or ""),
        "models": sorted(str(model) for model in models if str(model).strip()),
        "pricing_type": str(pricing_type or PHASE9_PRICING_TYPE),
        "credits_per_request_wei": max(0, int(credits_per_request_wei or 0)),
        "credits_per_request": _hub_credit_public_value_from_wei(credits_per_request_wei),
        "unit": "compute_credit",
        "execution_mode": str(execution_mode or PHASE9_EXECUTION_MODE),
    }
    if credits_per_token_wei is not None:
        seed_payload["credits_per_token_wei"] = max(0, int(credits_per_token_wei or 0))
        seed_payload["credits_per_token"] = _hub_credit_public_value_from_wei(credits_per_token_wei)
    seed = json.dumps(
        seed_payload,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return "offer_" + hashlib.sha256(seed).hexdigest()[:24]


def _phase9_worker_offer_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    capabilities = dict(payload.get("capabilities", {})) if isinstance(payload.get("capabilities"), dict) else {}
    pricing = dict(capabilities.get("pricing", {})) if isinstance(capabilities.get("pricing"), dict) else {}
    if bool(capabilities.get("phase9_unpriced", False)):
        return {}
    pricing_type = str(pricing.get("pricing_type") or pricing.get("type") or PHASE9_PRICING_TYPE).strip()
    if pricing_type in {"", "none", "unpriced", "unpriced_v0"}:
        return {}
    target_output_tokens = _hub_pricing_target_output_tokens(
        pricing,
        {**payload, "target_output_tokens": capabilities.get("target_output_tokens", payload.get("target_output_tokens"))},
        1024,
    )
    token_priced = (
        "token" in pricing_type.lower()
        or pricing.get("credits_per_token") not in (None, "")
        or pricing.get("credits_per_token_wei") not in (None, "")
        or payload.get("credits_per_token") not in (None, "")
        or payload.get("credits_per_token_wei") not in (None, "")
    )
    credits_per_token_wei = _hub_pricing_credit_per_token_wei(pricing, payload, "0.001") if token_priced else None
    credits_wei = _hub_pricing_credit_wei(
        pricing,
        {
            **payload,
            "target_output_tokens": target_output_tokens,
        },
        "1",
    )
    if credits_wei <= 0:
        return {}
    credits = _hub_credit_public_value_from_wei(credits_wei)
    credits_display = _hub_credit_display_from_wei(credits_wei)
    models = [
        str(model).strip()
        for model in (payload.get("models") if isinstance(payload.get("models"), list) else [])
        if str(model).strip()
    ]
    model = str(payload.get("model", "") or "").strip()
    if model and model not in models:
        models.insert(0, model)
    if not models:
        return {}
    execution = dict(capabilities.get("execution", {})) if isinstance(capabilities.get("execution"), dict) else {}
    execution_mode = str(
        pricing.get("execution_mode")
        or execution.get("mode")
        or capabilities.get("execution_mode")
        or PHASE9_EXECUTION_MODE
    ).strip() or PHASE9_EXECUTION_MODE
    assigned_ring = None
    for candidate in (
        payload.get("assigned_ring"),
        payload.get("ring"),
        capabilities.get("assigned_ring"),
        capabilities.get("ring"),
        capabilities.get("requested_ring"),
    ):
        try:
            text = str(candidate).strip()
            if text:
                assigned_ring = int(text)
                break
        except (TypeError, ValueError):
            continue
    worker_node_id = str(payload.get("node_id", "") or "")
    worker_instance_id = str(payload.get("worker_instance_id") or worker_node_id)
    offer = {
        "offer_id": _phase9_offer_id(
            worker_node_id=worker_instance_id,
            models=models,
            credits_per_request_wei=credits_wei,
            execution_mode=execution_mode,
            pricing_type=pricing_type,
            credits_per_token_wei=credits_per_token_wei,
        ),
        "worker_node_id": worker_node_id,
        "worker_instance_id": worker_instance_id,
        "seller_kind": "hub_connected_worker",
        "models": models,
        "capabilities": [str(item) for item in capabilities.get("capabilities", [])]
        if isinstance(capabilities.get("capabilities"), list)
        else ["chat.completions"],
        "pricing_type": pricing_type,
        "credits_per_request": credits,
        "credits_per_request_wei": str(credits_wei),
        "credits_per_request_display": credits_display,
        "estimated_credits_per_request": credits,
        "estimated_credits_per_request_wei": str(credits_wei),
        "estimated_credits_per_request_display": credits_display,
        "target_output_tokens": target_output_tokens,
        "unit": "compute_credit",
        "execution_mode": execution_mode,
        "price_source": "worker_registration",
        "settlement": {
            "earning_mode": "worker_earning_v0",
            "claim_mode": "worker_claim_v0",
            "settlement_mode": "rounded_batch_v0",
        },
    }
    if credits_per_token_wei is not None:
        offer["credits_per_token"] = _hub_credit_public_value_from_wei(credits_per_token_wei)
        offer["credits_per_token_wei"] = str(credits_per_token_wei)
        offer["credits_per_token_display"] = _hub_credit_display_from_wei(credits_per_token_wei)
    if assigned_ring is not None:
        offer["assigned_ring"] = assigned_ring
    return offer


def _clean_node_id(value: str, *, default: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip().lower())
    return text or default


def _require_allowed_transport(url: str, *, role: str, allow_insecure_dev_network: bool = False) -> None:
    if not hub_transport_is_encrypted_or_loopback(
        url,
        allow_insecure_dev_network=allow_insecure_dev_network,
    ):
        raise ValueError(
            f"{role} endpoint must use HTTPS, except for local loopback development URLs. "
            "Set MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK=1 only for local Docker/dev networks."
        )


@dataclass(frozen=True)
class HubWorker:
    node_id: str
    endpoint: str
    worker_instance_id: str = ""
    model: str = ""
    models: list[str] = field(default_factory=list)
    status: str = "available"
    credits_per_request: int | str = 1
    settlement_precision_places: int = DEFAULT_WORKER_PAYOUT_PRECISION_PLACES
    registered_at: str = ""
    last_seen_at: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    offer: dict[str, Any] = field(default_factory=dict)
    queue_depth: int = 0
    active_requests: int = 0
    max_concurrency: int = 1
    lease_expires_at: str = ""
    stale: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data.get("worker_instance_id"):
            data["worker_instance_id"] = data.get("node_id", "")
        offer = dict(self.offer) if self.offer else _phase9_worker_offer_from_payload(data)
        if offer:
            data["offer"] = offer
        else:
            data.pop("offer", None)
        return data


@dataclass(frozen=True)
class HubUpstream:
    node_id: str
    endpoint: str
    status: str = "available"
    credits_per_request: int | str = 1
    settlement_precision_places: int = DEFAULT_WORKER_PAYOUT_PRECISION_PLACES
    registered_at: str = ""
    last_seen_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hub_worker_is_pull_mode(capabilities: dict[str, Any] | None) -> bool:
    caps = capabilities if isinstance(capabilities, dict) else {}
    execution = caps.get("execution") if isinstance(caps.get("execution"), dict) else {}
    mode = str(execution.get("mode") or caps.get("execution_mode") or "").strip().lower()
    return mode == "worker_pull_v0"
