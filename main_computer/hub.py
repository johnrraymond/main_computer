from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.credit_units import (
    CREDIT_WEI_PER_CREDIT,
    credit_decimal_text_to_wei,
    credit_wei_product,
    credit_wei_to_decimal_text,
    credit_wei_to_display_text,
    credit_wei_to_whole_credits_floor,
)
from main_computer.energy import EnergyCreditLedger
from main_computer.hub_security import (
    HUB_SECURITY_PROFILE,
    decrypt_hub_envelope,
    derive_hub_session_key,
    encrypt_hub_envelope,
    generate_hub_session_keypair,
    hub_transport_is_encrypted_or_loopback,
)
from main_computer.hub_admin_site import HUB_ADMIN_ROUTES, build_admin_bootstrap_payload, render_hub_admin_html
from main_computer.hub_credit_bridge_completion import HubCreditBridgeCompletionService
from main_computer.hub_bridge_backend import HubBridgeBackendError, build_hub_bridge_backend
from main_computer.hub_credit_indexer import HubCreditIndexer, wallet_account_id
from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.hub_credit_models import (
    DEFAULT_WORKER_PAYOUT_PRECISION_PLACES,
    stable_id,
    normalize_worker_payout_precision_places,
)
from main_computer.multisession_key_signing import normalize_address, normalize_chain_id, parse_iso_datetime, recover_personal_sign_address, verify_personal_sign_blob
from main_computer.hub_plex_models import HubAIRequest, HubWorkerSummary
from main_computer.hub_plex_service import AIRequestPlexService
from main_computer.ring_admission import (
    RingAdmissionConfig,
    RingAdmissionDecision,
    load_ring_admission_config,
    normalize_requested_ring,
)
from main_computer.models import ChatAttachment, ChatMessage, ChatResponse


DEFAULT_HUB_PORT = 8770
DEFAULT_HUB_WORKER_PORT = 8771
HUB_WORKER_CHAT_PATH = "/api/hub/worker/chat"
HUB_WORKER_SESSION_START_PATH = "/api/hub/worker/sessions/start"
HUB_WORKER_SESSION_CHAT_PATH = "/api/hub/worker/sessions/chat"

ALLFATHER_HUB_REMOTE_MANIFEST_PATH = Path(__file__).resolve().parent / "config" / "allfather_hub_remote_manifest.json"


def load_allfather_hub_remote_manifest() -> dict[str, Any]:
    """Load the allfather build-time source manifest embedded with this Hub."""

    path = ALLFATHER_HUB_REMOTE_MANIFEST_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "available": False,
            "path": str(path),
            "reason": "allfather hub remote manifest is not present",
            "observed": {
                "current_directory_dirty_in_hub_remote_manifest": None,
                "current_directory_compared_to_remote_main": False,
            },
            "comparison": {
                "current_directory_dirty_vs_remote_main": None,
            },
        }
    except Exception as exc:
        return {
            "available": False,
            "path": str(path),
            "reason": str(exc),
            "observed": {
                "current_directory_dirty_in_hub_remote_manifest": None,
                "current_directory_compared_to_remote_main": False,
            },
            "comparison": {
                "current_directory_dirty_vs_remote_main": None,
            },
        }
    if not isinstance(payload, dict):
        return {
            "available": False,
            "path": str(path),
            "reason": "manifest was not a JSON object",
            "observed": {
                "current_directory_dirty_in_hub_remote_manifest": None,
                "current_directory_compared_to_remote_main": False,
            },
            "comparison": {
                "current_directory_dirty_vs_remote_main": None,
            },
        }
    payload = dict(payload)
    payload["available"] = True
    payload.setdefault("path", str(path))
    return payload


HUB_WORKER_STALE_AFTER_SECONDS = 90.0
HUB_WORKER_LEASE_SECONDS = 600.0
HUB_WORKER_INSTANCE_SLOT_LIMIT = 1
PHASE9_PRICING_MODE = "market_offer_fixed_per_call_v0"
PHASE9_PRICING_TYPE = "fixed_per_call_v0"
PHASE9_EXECUTION_MODE = "worker_pull_v0"
HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID = normalize_chain_id("0x28757b2")
HUB_MULTISESSION_KEY_MAX_AGE_MINUTES = 15
HUB_WORKER_API_USER_AGENT = "main-computer-worker-cli/1.0 (+https://greatlibrary.io)"


def _hub_worker_api_headers(*, json_body: bool = False) -> dict[str, str]:
    """Return explicit API-client headers for remote Hub worker-pull requests."""

    user_agent = str(os.environ.get("MAIN_COMPUTER_HUB_USER_AGENT") or "").strip() or HUB_WORKER_API_USER_AGENT
    headers = {
        "Accept": "application/json",
        "User-Agent": user_agent,
        "X-Main-Computer-Client": "main-computer-worker-cli",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _hub_worker_http_error_message(url: str, exc: HTTPError, body: str) -> str:
    message = f"Hub request failed for {url} with HTTP {exc.code}: {body}"
    if exc.code == 403 and "error code: 1010" in body.lower():
        message += (
            "\nCloudflare rejected this HTTP client signature. The worker command now sends explicit API client "
            "headers; if this still fails, the mainnet hub Cloudflare rules need to allow "
            f"{HUB_WORKER_API_USER_AGENT!r} or set MAIN_COMPUTER_HUB_USER_AGENT to an allowed client signature."
        )
    return message



class HubPaymentRequired(ValueError):
    """Raised when a paid Hub request cannot reserve enough wallet credits."""


class HubCreditAuthorizationError(ValueError):
    """Raised when a paid Hub request is not authorized for the claimed wallet."""


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


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _clean_node_id(value: str, *, default: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value or "").strip().lower())
    return text or default


def _stable_request_id(payload: dict[str, Any]) -> str:
    seed = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    stamp = str(time.time_ns()).encode("ascii")
    return "hub_" + hashlib.sha256(seed + stamp).hexdigest()[:20]


def _stable_session_id(request_id: str, payload: dict[str, Any]) -> str:
    seed = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    stamp = str(time.time_ns()).encode("ascii")
    return "sess_" + hashlib.sha256(request_id.encode("utf-8") + seed + stamp).hexdigest()[:24]


def _require_allowed_transport(url: str, *, role: str, allow_insecure_dev_network: bool = False) -> None:
    if not hub_transport_is_encrypted_or_loopback(
        url,
        allow_insecure_dev_network=allow_insecure_dev_network,
    ):
        raise ValueError(
            f"{role} endpoint must use HTTPS, except for local loopback development URLs. "
            "Set MAIN_COMPUTER_HUB_ALLOW_INSECURE_DEV_NETWORK=1 only for local Docker/dev networks."
        )


def chat_message_to_dict(message: ChatMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "attachments": [asdict(attachment) for attachment in message.attachments],
    }


def chat_message_from_dict(payload: dict[str, Any]) -> ChatMessage:
    attachments = [
        ChatAttachment(
            id=str(item.get("id", "")),
            filename=str(item.get("filename", "")),
            mime_type=str(item.get("mime_type", "application/octet-stream")),
            data_base64=str(item.get("data_base64", "")),
            kind=str(item.get("kind", "file")),
            metadata=dict(item.get("metadata", {})) if isinstance(item.get("metadata", {}), dict) else {},
        )
        for item in payload.get("attachments", []) or []
        if isinstance(item, dict)
    ]
    role = str(payload.get("role", "user"))
    if role not in {"system", "user", "assistant"}:
        role = "user"
    return ChatMessage(role=role, content=str(payload.get("content", "")), attachments=attachments)


def chat_response_from_dict(payload: dict[str, Any], *, default_provider: str, default_model: str) -> ChatResponse:
    return ChatResponse(
        content=str(payload.get("content", "")),
        provider=str(payload.get("provider") or default_provider),
        model=str(payload.get("model") or default_model),
        metadata=dict(payload.get("metadata", {})) if isinstance(payload.get("metadata", {}), dict) else {},
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


class HubRegistry:
    """JSON-backed worker registry used by the hub server.

    Phase 2 keeps this class as the stable compatibility surface and extends it
    with worker heartbeats, leases, capacity metadata, and stale-worker
    filtering. The registry still stores routing metadata only; in high-security
    mode prompt-bearing data remains inside encrypted envelopes.
    """

    def __init__(self, root: Path, *, allow_insecure_dev_network: bool = False) -> None:
        self.root = root
        self.path = root / "hub_workers.json"
        self.allow_insecure_dev_network = bool(allow_insecure_dev_network)
        self.worker_stale_after_s = HUB_WORKER_STALE_AFTER_SECONDS
        self.worker_lease_s = HUB_WORKER_LEASE_SECONDS
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, Any]:
        self.expire_stale_workers()
        data = self._load()
        workers = data.get("workers", [])
        available_workers = [
            worker
            for worker in workers
            if str(worker.get("status", "available")) in {"available", "configured"}
            and not bool(worker.get("stale", False))
            and int(worker.get("active_requests", 0) or 0) < int(worker.get("max_concurrency", 1) or 1)
        ]
        stale_workers = [
            worker
            for worker in workers
            if bool(worker.get("stale", False)) or str(worker.get("status", "")).lower() == "stale"
        ]
        return {
            "ok": True,
            "hub": data.get("hub", {}),
            "workers": workers,
            "worker_count": len(workers),
            "available_worker_count": len(available_workers),
            "stale_worker_count": len(stale_workers),
            "upstream_hubs": data.get("upstream_hubs", []),
            "upstream_count": len(data.get("upstream_hubs", [])),
            "leases": {
                "worker_stale_after_seconds": self.worker_stale_after_s,
                "worker_lease_seconds": self.worker_lease_s,
            },
        }

    def register_worker(
        self,
        *,
        node_id: str,
        endpoint: str,
        model: str = "",
        models: list[str] | None = None,
        capabilities: dict[str, Any] | None = None,
        credits_per_request: Any = 1,
        settlement_precision_places: int | None = None,
        queue_depth: int = 0,
        active_requests: int = 0,
        max_concurrency: int = 1,
        worker_instance_id: str = "",
    ) -> HubWorker:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else clean_node_id
        clean_endpoint = str(endpoint or "").strip().rstrip("/")
        if not clean_endpoint:
            raise ValueError("Worker endpoint is required.")
        _require_allowed_transport(
            clean_endpoint,
            role="Worker",
            allow_insecure_dev_network=self.allow_insecure_dev_network,
        )
        now = _utc_now()
        clean_capabilities = dict(capabilities or {})
        credit_price_wei, credit_price, _credit_price_display, pricing_payload = _hub_normalized_pricing_payload(
            clean_capabilities,
            {"credits_per_request": credits_per_request},
            "1",
        )
        clean_capabilities["pricing"] = pricing_payload
        clean_models = self._normalize_models(model=model, models=models)
        primary_model = clean_models[0] if clean_models else str(model or "").strip()
        clean_capabilities.setdefault("worker_instance_id", clean_worker_instance_id)
        precision_source = (
            settlement_precision_places
            if settlement_precision_places is not None
            else clean_capabilities.get("settlement_precision_places", clean_capabilities.get("payout_precision_places", None))
        )
        clean_settlement_precision = normalize_worker_payout_precision_places(precision_source)
        clean_capabilities.setdefault("settlement_precision_places", clean_settlement_precision)
        # One worker registration represents one live worker-instance slot.
        # A second simultaneous AI request must come from a second worker connection
        # with its own node/instance identity, not a multi-slot max_concurrency bump.
        clean_max_concurrency = HUB_WORKER_INSTANCE_SLOT_LIMIT
        clean_active = min(max(0, int(active_requests or 0)), clean_max_concurrency)
        clean_queue_depth = max(0, int(queue_depth or 0))
        with self._lock:
            data = self._load()
            workers = [
                item
                for item in data["workers"]
                if str(item.get("worker_instance_id") or item.get("node_id") or "") != clean_worker_instance_id
            ]
            existing = next(
                (
                    item
                    for item in data["workers"]
                    if str(item.get("worker_instance_id") or item.get("node_id") or "") == clean_worker_instance_id
                ),
                {},
            )
            registered_at = str(existing.get("registered_at") or now)
            worker = HubWorker(
                node_id=clean_node_id,
                endpoint=clean_endpoint,
                worker_instance_id=clean_worker_instance_id,
                model=primary_model,
                models=clean_models,
                status="available" if clean_active < clean_max_concurrency else "busy",
                credits_per_request=credit_price,
                settlement_precision_places=clean_settlement_precision,
                registered_at=registered_at,
                last_seen_at=now,
                capabilities=clean_capabilities,
                offer=_phase9_worker_offer_from_payload(
                    {
                        "node_id": clean_node_id,
                        "worker_instance_id": clean_worker_instance_id,
                        "model": primary_model,
                        "models": clean_models,
                        "credits_per_request": credit_price,
                        "credits_per_request_wei": str(credit_price_wei),
                        "capabilities": clean_capabilities,
                    }
                ),
                queue_depth=clean_queue_depth,
                active_requests=clean_active,
                max_concurrency=clean_max_concurrency,
                lease_expires_at=str(existing.get("lease_expires_at", "") or ""),
                stale=False,
            )
            workers.append(worker.as_dict())
            data["workers"] = sorted(workers, key=lambda item: str(item.get("node_id", "")))
            self._save(data)
            return worker

    def heartbeat_worker(
        self,
        node_id: str,
        *,
        status: str = "available",
        model: str = "",
        models: list[str] | None = None,
        capabilities: dict[str, Any] | None = None,
        queue_depth: int | None = None,
        active_requests: int | None = None,
        max_concurrency: int | None = None,
        worker_instance_id: str = "",
    ) -> HubWorker:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else ""
        now = _utc_now()
        with self._lock:
            data = self._load()
            for item in data["workers"]:
                item_instance_id = str(item.get("worker_instance_id") or item.get("node_id") or "")
                if clean_worker_instance_id:
                    if item_instance_id != clean_worker_instance_id:
                        continue
                elif item.get("node_id") != clean_node_id:
                    continue
                next_max = HUB_WORKER_INSTANCE_SLOT_LIMIT
                next_active = max(0, int(item.get("active_requests", 0) or 0)) if active_requests is None else max(0, int(active_requests or 0))
                next_active = min(next_active, next_max)
                clean_status = str(status or item.get("status") or "available").strip().lower()
                if clean_status not in {"available", "configured", "busy", "offline", "draining"}:
                    clean_status = "available"
                if clean_status == "available" and next_active >= next_max:
                    clean_status = "busy"
                item["status"] = clean_status
                item["last_seen_at"] = now
                item["stale"] = False
                item["queue_depth"] = max(0, int(item.get("queue_depth", 0) or 0)) if queue_depth is None else max(0, int(queue_depth or 0))
                item["active_requests"] = next_active
                item["max_concurrency"] = next_max
                if capabilities is not None:
                    item["capabilities"] = dict(capabilities)
                if models is not None or model:
                    clean_models = self._normalize_models(model=model or str(item.get("model", "")), models=models)
                    item["models"] = clean_models
                    item["model"] = clean_models[0] if clean_models else str(model or "")
                worker = self._worker_from_payload(item)
                self._save(data)
                return worker
        raise KeyError(f"Unknown hub worker: {clean_node_id}")

    def get_worker(self, node_id: str, *, worker_instance_id: str = "") -> HubWorker | None:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else ""
        self.expire_stale_workers()
        data = self._load()
        if clean_worker_instance_id:
            for item in data["workers"]:
                if str(item.get("worker_instance_id") or item.get("node_id") or "") == clean_worker_instance_id:
                    return self._worker_from_payload(item)
            return None
        matches = [item for item in data["workers"] if item.get("node_id") == clean_node_id]
        if len(matches) == 1:
            return self._worker_from_payload(matches[0])
        for item in matches:
            if str(item.get("worker_instance_id") or item.get("node_id") or "") == clean_node_id:
                return self._worker_from_payload(item)
        return None

    def expire_stale_workers(self, *, stale_after_s: float | None = None) -> int:
        threshold = self.worker_stale_after_s if stale_after_s is None else max(0.0, float(stale_after_s))
        now = datetime.now(tz=timezone.utc)
        changed = 0
        with self._lock:
            data = self._load()
            for worker in data["workers"]:
                status = str(worker.get("status", "available")).lower()
                if status in {"offline", "draining"}:
                    continue
                last_seen = self._parse_iso(str(worker.get("last_seen_at", "") or worker.get("registered_at", "")))
                lease_expires = self._parse_iso(str(worker.get("lease_expires_at", "") or ""))
                heartbeat_age = (now - last_seen).total_seconds() if last_seen is not None else None
                heartbeat_stale = heartbeat_age is not None and (
                    heartbeat_age > threshold or (threshold == 0 and heartbeat_age >= 0)
                )
                lease_stale = lease_expires is not None and lease_expires < now and int(worker.get("active_requests", 0) or 0) > 0
                if heartbeat_stale or lease_stale:
                    worker["status"] = "stale"
                    worker["stale"] = True
                    worker["active_requests"] = 0
                    worker["lease_expires_at"] = ""
                    changed += 1
            if changed:
                self._save(data)
        return changed

    def register_upstream_hub(
        self,
        *,
        node_id: str,
        endpoint: str,
        credits_per_request: Any = 1,
    ) -> HubUpstream:
        clean_node_id = _clean_node_id(node_id, default="upstream-hub")
        clean_endpoint = str(endpoint or "").strip().rstrip("/")
        if not clean_endpoint:
            raise ValueError("Upstream hub endpoint is required.")
        _require_allowed_transport(
            clean_endpoint,
            role="Upstream hub",
            allow_insecure_dev_network=self.allow_insecure_dev_network,
        )
        now = _utc_now()
        credit_price_wei = _hub_credit_wei_from_value(credits_per_request, "1", minimum_wei=1)
        credit_price = _hub_credit_public_value_from_wei(credit_price_wei)
        with self._lock:
            data = self._load()
            upstreams = [item for item in data["upstream_hubs"] if item.get("node_id") != clean_node_id]
            existing = next((item for item in data["upstream_hubs"] if item.get("node_id") == clean_node_id), {})
            registered_at = str(existing.get("registered_at") or now)
            upstream = HubUpstream(
                node_id=clean_node_id,
                endpoint=clean_endpoint,
                status="available",
                credits_per_request=credit_price,
                registered_at=registered_at,
                last_seen_at=now,
            )
            upstreams.append(upstream.as_dict())
            data["upstream_hubs"] = sorted(upstreams, key=lambda item: str(item.get("node_id", "")))
            self._save(data)
            return upstream

    def mark_upstream_hub(self, node_id: str, *, status: str) -> None:
        clean_node_id = _clean_node_id(node_id, default="upstream-hub")
        with self._lock:
            data = self._load()
            changed = False
            for upstream in data["upstream_hubs"]:
                if upstream.get("node_id") == clean_node_id:
                    upstream["status"] = status
                    upstream["last_seen_at"] = _utc_now()
                    changed = True
            if changed:
                self._save(data)

    def select_upstream_hub(self) -> HubUpstream | None:
        data = self._load()
        available: list[HubUpstream] = []
        for item in data["upstream_hubs"]:
            if str(item.get("status", "available")) not in {"available", "configured"}:
                continue
            available.append(
                HubUpstream(
                    node_id=str(item.get("node_id", "")),
                    endpoint=str(item.get("endpoint", "")).rstrip("/"),
                    status=str(item.get("status", "available")),
                    credits_per_request=_hub_credit_public_value_from_wei(
                        _hub_credit_wei_from_explicit_or_decimal(item.get("credits_per_request_wei"), item.get("credits_per_request", 1), "1", minimum_wei=1)
                    ),
                    registered_at=str(item.get("registered_at", "")),
                    last_seen_at=str(item.get("last_seen_at", "")),
                )
            )
        return sorted(available, key=lambda upstream: upstream.last_seen_at or upstream.registered_at)[0] if available else None

    def mark_worker(self, node_id: str, *, status: str, worker_instance_id: str = "") -> None:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else ""
        clean_status = str(status or "available").strip().lower()
        if clean_status not in {"available", "configured", "busy", "offline", "stale", "draining"}:
            clean_status = "available"
        with self._lock:
            data = self._load()
            changed = False
            for worker in data["workers"]:
                item_instance_id = str(worker.get("worker_instance_id") or worker.get("node_id") or "")
                if clean_worker_instance_id:
                    if item_instance_id != clean_worker_instance_id:
                        continue
                elif worker.get("node_id") != clean_node_id:
                    continue
                worker["status"] = clean_status
                worker["last_seen_at"] = _utc_now()
                worker["stale"] = clean_status == "stale"
                if clean_status in {"offline", "stale", "draining"}:
                    worker["active_requests"] = 0
                    worker["lease_expires_at"] = ""
                changed = True
            if changed:
                self._save(data)

    def lease_worker(
        self,
        model: str = "",
        *,
        request_id: str = "",
        preferred_node_id: str = "",
        preferred_worker_instance_id: str = "",
        lease_seconds: float | None = None,
    ) -> HubWorker | None:
        desired = str(model or "").strip()
        preferred = _clean_node_id(preferred_node_id, default="") if preferred_node_id else ""
        preferred_instance = _clean_node_id(preferred_worker_instance_id, default="") if preferred_worker_instance_id else ""
        lease_s = self.worker_lease_s if lease_seconds is None else max(1.0, float(lease_seconds))
        with self._lock:
            self._expire_stale_workers_unlocked(self._load(), stale_after_s=self.worker_stale_after_s)
            data = self._load()
            candidates = [
                item
                for item in data["workers"]
                if self._is_worker_lease_candidate(
                    item,
                    desired=desired,
                    preferred_node_id=preferred,
                    preferred_worker_instance_id=preferred_instance,
                    allow_model_fallback=False,
                )
            ]
            if not candidates and desired and not preferred and not preferred_instance:
                candidates = [
                    item
                    for item in data["workers"]
                    if self._is_worker_lease_candidate(
                        item,
                        desired="",
                        preferred_node_id="",
                        preferred_worker_instance_id="",
                        allow_model_fallback=True,
                    )
                ]
            if not candidates:
                return None
            worker = sorted(
                candidates,
                key=lambda item: (
                    max(0, int(item.get("queue_depth", 0) or 0)) + max(0, int(item.get("active_requests", 0) or 0)),
                    str(item.get("last_seen_at", "") or item.get("registered_at", "")),
                    str(item.get("node_id", "")),
                    str(item.get("worker_instance_id") or item.get("node_id") or ""),
                ),
            )[0]
            max_concurrency = max(1, int(worker.get("max_concurrency", 1) or 1))
            active = min(max_concurrency, max(0, int(worker.get("active_requests", 0) or 0)) + 1)
            worker["active_requests"] = active
            worker["status"] = "busy" if active >= max_concurrency else "available"
            worker["lease_expires_at"] = (datetime.now(tz=timezone.utc) + timedelta(seconds=lease_s)).isoformat()
            worker["last_seen_at"] = _utc_now()
            worker["stale"] = False
            if request_id:
                worker["last_request_id"] = str(request_id)
            self._save(data)
            return self._worker_from_payload(worker)

    def release_worker(self, node_id: str, *, request_id: str = "", success: bool = True, worker_instance_id: str = "") -> None:
        clean_node_id = _clean_node_id(node_id, default="hub-worker")
        clean_worker_instance_id = _clean_node_id(worker_instance_id, default="") if worker_instance_id else ""
        with self._lock:
            data = self._load()
            changed = False
            for worker in data["workers"]:
                item_instance_id = str(worker.get("worker_instance_id") or worker.get("node_id") or "")
                if clean_worker_instance_id:
                    if item_instance_id != clean_worker_instance_id:
                        continue
                elif worker.get("node_id") != clean_node_id:
                    continue
                max_concurrency = max(1, int(worker.get("max_concurrency", 1) or 1))
                active = max(0, int(worker.get("active_requests", 0) or 0) - 1)
                worker["active_requests"] = active
                worker["status"] = "available" if success else "offline"
                if active >= max_concurrency:
                    worker["status"] = "busy"
                if not success:
                    worker["active_requests"] = 0
                    worker["lease_expires_at"] = ""
                elif active == 0:
                    worker["lease_expires_at"] = ""
                worker["last_seen_at"] = _utc_now()
                worker["stale"] = False
                if request_id:
                    worker["last_request_id"] = str(request_id)
                changed = True
            if changed:
                self._save(data)

    def select_worker(self, model: str = "") -> HubWorker | None:
        desired = str(model or "").strip()
        self.expire_stale_workers()
        data = self._load()
        available: list[HubWorker] = []
        for item in data["workers"]:
            if not self._is_worker_lease_candidate(item, desired=desired, preferred_node_id="", allow_model_fallback=False):
                continue
            available.append(self._worker_from_payload(item))
        if not available and desired:
            return self.select_worker("")
        return sorted(
            available,
            key=lambda worker: (worker.queue_depth + worker.active_requests, worker.last_seen_at or worker.registered_at),
        )[0] if available else None

    def record_ring_admission_rejection(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        payload.setdefault("event_type", "ring_admission_rejected")
        payload.setdefault("created_at", _utc_now())
        seed = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        payload.setdefault("event_id", "ring-admission-" + hashlib.sha256(seed).hexdigest()[:24])
        audit_path = self.root / "ring_admission_audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return payload

    def list_ring_admission_audit(self, *, limit: int = 500) -> list[dict[str, Any]]:
        audit_path = self.root / "ring_admission_audit.jsonl"
        if not audit_path.exists():
            return []
        clean_limit = max(1, int(limit or 500))
        events: list[dict[str, Any]] = []
        try:
            lines = audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines[-clean_limit:]:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    def ring_admission_audit_count(self) -> int:
        audit_path = self.root / "ring_admission_audit.jsonl"
        if not audit_path.exists():
            return 0
        try:
            return sum(1 for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            return 0

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return self._normalize(data)
            except (OSError, json.JSONDecodeError):
                pass
        return self._normalize({})

    def _save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(self._normalize(data), ensure_ascii=False, indent=2), encoding="utf-8")

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        hub = data.get("hub") if isinstance(data.get("hub"), dict) else {}
        created_at = str(hub.get("created_at") or _utc_now())
        workers = data.get("workers") if isinstance(data.get("workers"), list) else []
        normalized_workers: list[dict[str, Any]] = []
        for item in workers:
            if not isinstance(item, dict):
                continue
            endpoint = str(item.get("endpoint", "")).strip().rstrip("/")
            node_id = _clean_node_id(str(item.get("node_id", "")), default="")
            worker_instance_id = _clean_node_id(str(item.get("worker_instance_id") or node_id), default=node_id)
            if not node_id or not endpoint:
                continue
            models = self._normalize_models(model=str(item.get("model", "") or ""), models=item.get("models") if isinstance(item.get("models"), list) else None)
            primary_model = models[0] if models else str(item.get("model", "") or "")
            max_concurrency = HUB_WORKER_INSTANCE_SLOT_LIMIT
            active_requests = min(max_concurrency, max(0, int(item.get("active_requests", 0) or 0)))
            status = str(item.get("status", "available") or "available").lower()
            if status not in {"available", "configured", "busy", "offline", "stale", "draining"}:
                status = "available"
            capabilities = dict(item.get("capabilities", {})) if isinstance(item.get("capabilities"), dict) else {}
            price_wei, price, price_display, pricing_payload = _hub_normalized_pricing_payload(capabilities, item, "1")
            capabilities["pricing"] = pricing_payload
            normalized_worker = {
                "node_id": node_id,
                "worker_instance_id": worker_instance_id,
                "endpoint": endpoint,
                "model": primary_model,
                "models": models,
                "status": status,
                "credits_per_request": price,
                "credits_per_request_wei": str(price_wei),
                "credits_per_request_display": price_display,
                "settlement_precision_places": normalize_worker_payout_precision_places(
                    item.get("settlement_precision_places")
                    if item.get("settlement_precision_places") is not None
                    else (
                        item.get("capabilities", {}).get("settlement_precision_places")
                        if isinstance(item.get("capabilities"), dict)
                        else None
                    )
                ),
                "registered_at": str(item.get("registered_at") or created_at),
                "last_seen_at": str(item.get("last_seen_at") or item.get("registered_at") or created_at),
                "capabilities": capabilities,
                "queue_depth": max(0, int(item.get("queue_depth", 0) or 0)),
                "active_requests": active_requests,
                "max_concurrency": max_concurrency,
                "lease_expires_at": str(item.get("lease_expires_at", "") or ""),
                "stale": bool(item.get("stale", False)) or status == "stale",
            }
            offer = _phase9_worker_offer_from_payload(normalized_worker)
            if offer:
                normalized_worker["offer"] = offer
            normalized_workers.append(normalized_worker)
        upstream_hubs = data.get("upstream_hubs") if isinstance(data.get("upstream_hubs"), list) else []
        normalized_upstreams: list[dict[str, Any]] = []
        for item in upstream_hubs:
            if not isinstance(item, dict):
                continue
            endpoint = str(item.get("endpoint", "")).strip().rstrip("/")
            node_id = _clean_node_id(str(item.get("node_id", "")), default="")
            if not node_id or not endpoint:
                continue
            normalized_upstreams.append(
                {
                    "node_id": node_id,
                    "endpoint": endpoint,
                    "status": str(item.get("status", "available") or "available"),
                    "credits_per_request": _hub_credit_public_value_from_wei(
                        _hub_credit_wei_from_explicit_or_decimal(item.get("credits_per_request_wei"), item.get("credits_per_request", 1), "1", minimum_wei=1)
                    ),
                    "registered_at": str(item.get("registered_at") or created_at),
                    "last_seen_at": str(item.get("last_seen_at") or item.get("registered_at") or created_at),
                }
            )
        return {
            "hub": {
                "name": str(hub.get("name") or "main-computer-hub"),
                "created_at": created_at,
                "settlement": "batched-worker-claim",
                "security_profile": HUB_SECURITY_PROFILE,
            },
            "workers": sorted(normalized_workers, key=lambda item: item["node_id"]),
            "upstream_hubs": sorted(normalized_upstreams, key=lambda item: item["node_id"]),
        }

    def _expire_stale_workers_unlocked(self, data: dict[str, Any], *, stale_after_s: float) -> int:
        threshold = max(0.0, float(stale_after_s))
        now = datetime.now(tz=timezone.utc)
        changed = 0
        for worker in data["workers"]:
            status = str(worker.get("status", "available")).lower()
            if status in {"offline", "draining"}:
                continue
            last_seen = self._parse_iso(str(worker.get("last_seen_at", "") or worker.get("registered_at", "")))
            lease_expires = self._parse_iso(str(worker.get("lease_expires_at", "") or ""))
            heartbeat_stale = last_seen is not None and (now - last_seen).total_seconds() > threshold
            lease_stale = lease_expires is not None and lease_expires < now and int(worker.get("active_requests", 0) or 0) > 0
            if heartbeat_stale or lease_stale:
                worker["status"] = "stale"
                worker["stale"] = True
                worker["active_requests"] = 0
                worker["lease_expires_at"] = ""
                changed += 1
        if changed:
            self._save(data)
        return changed

    def _is_worker_lease_candidate(
        self,
        item: dict[str, Any],
        *,
        desired: str,
        preferred_node_id: str,
        preferred_worker_instance_id: str = "",
        allow_model_fallback: bool = False,
    ) -> bool:
        status = str(item.get("status", "available")).lower()
        if status not in {"available", "configured"}:
            return False
        if bool(item.get("stale", False)):
            return False
        active = max(0, int(item.get("active_requests", 0) or 0))
        max_concurrency = max(1, int(item.get("max_concurrency", 1) or 1))
        if active >= max_concurrency:
            return False
        node_id = str(item.get("node_id", ""))
        worker_instance_id = str(item.get("worker_instance_id") or node_id)
        if preferred_worker_instance_id and worker_instance_id != preferred_worker_instance_id:
            return False
        if preferred_node_id and node_id != preferred_node_id and worker_instance_id != preferred_node_id:
            return False
        if not desired or allow_model_fallback:
            return True
        worker_models = [str(model).strip() for model in item.get("models", []) if str(model).strip()]
        worker_model = str(item.get("model", "") or "").strip()
        if worker_model and worker_model not in worker_models:
            worker_models.append(worker_model)
        return desired in worker_models

    def _worker_from_payload(self, item: dict[str, Any]) -> HubWorker:
        models = [str(model).strip() for model in item.get("models", []) if str(model).strip()]
        model = str(item.get("model", "") or "")
        if model and model not in models:
            models.insert(0, model)
        return HubWorker(
            node_id=str(item.get("node_id", "")),
            endpoint=str(item.get("endpoint", "")).rstrip("/"),
            worker_instance_id=str(item.get("worker_instance_id") or item.get("node_id", "")),
            model=model,
            models=models,
            status=str(item.get("status", "available")),
            credits_per_request=_hub_credit_public_value_from_wei(_hub_pricing_credit_wei(dict(item.get("capabilities", {}).get("pricing", {})) if isinstance(item.get("capabilities", {}), dict) and isinstance(item.get("capabilities", {}).get("pricing", {}), dict) else {}, item, "1")),
            settlement_precision_places=normalize_worker_payout_precision_places(
                item.get("settlement_precision_places")
                if item.get("settlement_precision_places") is not None
                else (
                    item.get("capabilities", {}).get("settlement_precision_places")
                    if isinstance(item.get("capabilities"), dict)
                    else None
                )
            ),
            registered_at=str(item.get("registered_at", "")),
            last_seen_at=str(item.get("last_seen_at", "")),
            capabilities=dict(item.get("capabilities", {})) if isinstance(item.get("capabilities"), dict) else {},
            offer=dict(item.get("offer", {})) if isinstance(item.get("offer"), dict) else _phase9_worker_offer_from_payload(item),
            queue_depth=max(0, int(item.get("queue_depth", 0) or 0)),
            active_requests=max(0, int(item.get("active_requests", 0) or 0)),
            max_concurrency=max(1, int(item.get("max_concurrency", 1) or 1)),
            lease_expires_at=str(item.get("lease_expires_at", "") or ""),
            stale=bool(item.get("stale", False)) or str(item.get("status", "")).lower() == "stale",
        )

    @staticmethod
    def _normalize_models(*, model: str = "", models: list[str] | None = None) -> list[str]:
        result: list[str] = []
        for raw in [model, *(models or [])]:
            clean = str(raw or "").strip()
            if clean and clean not in result:
                result.append(clean)
        return result

    @staticmethod
    def _parse_iso(value: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


class HubDispatcher:
    """Compatibility facade around the hub AI request plexing service."""

    def __init__(
        self,
        registry: HubRegistry,
        ledger: EnergyCreditLedger,
        *,
        timeout_s: float = 600.0,
        allow_insecure_dev_network: bool = False,
        credit_ledger: HubCreditLedger | None = None,
        default_credits_per_request: int = 1,
        request_store: Any | None = None,
        quote_store: Any | None = None,
        secure_session_store: Any | None = None,
        feedback_store: Any | None = None,
    ) -> None:
        self.registry = registry
        self.ledger = ledger
        self.timeout_s = max(1.0, float(timeout_s or 600.0))
        self.allow_insecure_dev_network = bool(allow_insecure_dev_network)
        self.plex_service = AIRequestPlexService(
            registry,
            ledger,
            root=registry.root,
            timeout_s=self.timeout_s,
            allow_insecure_dev_network=self.allow_insecure_dev_network,
            credit_ledger=credit_ledger,
            default_credits_per_request=default_credits_per_request,
            request_store=request_store,
            quote_store=quote_store,
            secure_session_store=secure_session_store,
            feedback_store=feedback_store,
        )

    def quote(self, request: HubAIRequest) -> dict[str, Any]:
        return self.plex_service.quote_request(request)

    def chat(
        self,
        *,
        messages: Sequence[ChatMessage],
        model: str = "",
        client_node_id: str = "main-computer-client",
        hop_count: int = 0,
    ) -> ChatResponse:
        request = HubAIRequest.from_messages(
            list(messages),
            model=model,
            client_node_id=client_node_id,
            hop_count=hop_count,
        )
        return self.plex_service.dispatch_sync(request)

    def submit(self, request: HubAIRequest) -> dict[str, Any]:
        return self.plex_service.submit(request).as_dict()

    def submit_worker_pull(self, request: HubAIRequest) -> dict[str, Any]:
        return self.plex_service.submit_worker_pull(request).as_dict()

    def poll_worker(
        self,
        *,
        worker_node_id: str,
        worker_instance_id: str = "",
        lease_seconds: float | None = None,
    ) -> dict[str, Any]:
        return self.plex_service.poll_worker(
            worker_node_id=worker_node_id,
            worker_instance_id=worker_instance_id,
            lease_seconds=lease_seconds,
        )

    def submit_worker_result(
        self,
        *,
        worker_node_id: str,
        request_id: str,
        lease_id: str,
        result: dict[str, Any],
        worker_instance_id: str = "",
    ) -> dict[str, Any]:
        return self.plex_service.submit_worker_result(
            worker_node_id=worker_node_id,
            worker_instance_id=worker_instance_id,
            request_id=request_id,
            lease_id=lease_id,
            result=result,
        )

    def submit_worker_stream_event(
        self,
        *,
        worker_node_id: str,
        request_id: str,
        lease_id: str,
        event: dict[str, Any],
        worker_instance_id: str = "",
    ) -> dict[str, Any]:
        return self.plex_service.submit_worker_stream_event(
            worker_node_id=worker_node_id,
            worker_instance_id=worker_instance_id,
            request_id=request_id,
            lease_id=lease_id,
            event=event,
        )

    def get_request_status(self, request_id: str) -> dict[str, Any]:
        return self.plex_service.get_status(request_id).as_dict()

    def pickup_request_result(self, request_id: str, *, account_id: str = "", client_node_id: str = "") -> dict[str, Any]:
        return self.plex_service.pickup_completed_result(
            request_id,
            account_id=account_id,
            client_node_id=client_node_id,
        )

    def cancel_request(self, request_id: str) -> dict[str, Any]:
        return self.plex_service.cancel(request_id).as_dict()

    def list_requests(self, *, limit: int = 100, states: set[str] | None = None) -> list[dict[str, Any]]:
        return [status.as_dict() for status in self.plex_service.list_statuses(limit=limit, states=states)]

    def get_request_events(self, request_id: str) -> list[dict[str, Any]]:
        return self.plex_service.get_events(request_id)

    def wait_for_request_events(
        self,
        request_id: str,
        *,
        after: int = 0,
        timeout_s: float = 30.0,
    ):
        return self.plex_service.wait_for_events(request_id, after=after, timeout_s=timeout_s)

    def submit_request_feedback(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.plex_service.submit_requester_feedback(request_id, payload)

    def get_request_feedback(self, request_id: str, *, account_id: str = "") -> dict[str, Any]:
        return self.plex_service.get_request_feedback(request_id, account_id=account_id)

    def worker_reliability_summary(
        self,
        *,
        worker_node_id: str = "",
        worker_commitment: str = "",
        include_private: bool = False,
        limit: int = 500,
    ) -> dict[str, Any]:
        return self.plex_service.worker_reliability_summary(
            worker_node_id=worker_node_id,
            worker_commitment=worker_commitment,
            include_private=include_private,
            limit=limit,
        )

    def ring_control_feedback_summary(self, *, limit: int = 500) -> dict[str, Any]:
        return self.plex_service.ring_control_feedback_summary(limit=limit)

    def metrics(self) -> dict[str, Any]:
        return self.plex_service.metrics()

    def start_secure_session(
        self,
        *,
        requester_public_key: str,
        model: str = "",
        client_node_id: str = "main-computer-client",
        hop_count: int = 0,
    ) -> dict[str, Any]:
        return self.plex_service.start_secure_session(
            requester_public_key=requester_public_key,
            model=model,
            client_node_id=client_node_id,
            hop_count=hop_count,
        )

    def secure_chat(self, *, session_id: str, request_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        return self.plex_service.secure_chat(session_id=session_id, request_id=request_id, envelope=envelope)


class _JsonHandler(BaseHTTPRequestHandler):
    server_version = "MainComputerHub/0.2"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        if getattr(self.server, "verbose", False):
            super().log_message(format, *args)

    def _worker_route_diagnostics_enabled(self) -> bool:
        raw = str(os.environ.get("HUB_WORKER_ROUTE_DIAGNOSTICS", "")).strip().lower()
        if raw in {"0", "false", "no", "off"}:
            return False
        if raw in {"1", "true", "yes", "on"}:
            return True
        return bool(getattr(self.server, "worker_route_diagnostics", False))

    def _next_worker_route_diag_id(self) -> int:
        lock = getattr(self.server, "worker_route_diag_lock", None)
        if lock is None:
            self.server.worker_route_diag_lock = threading.Lock()
            self.server.worker_route_diag_sequence = 0
            lock = self.server.worker_route_diag_lock
        with lock:
            value = int(getattr(self.server, "worker_route_diag_sequence", 0) or 0) + 1
            self.server.worker_route_diag_sequence = value
            return value

    def _worker_route_diag_start(self, route: str, path: str) -> tuple[int, float]:
        diag_id = self._next_worker_route_diag_id()
        started_at = time.perf_counter()
        self._worker_route_diag_step(diag_id, route, "start", started_at, path=path)
        return diag_id, started_at

    def _worker_route_diag_step(self, diag_id: int, route: str, stage: str, started_at: float, **fields: Any) -> None:
        if not self._worker_route_diagnostics_enabled():
            return
        port = getattr(self.server, "server_port", "")
        payload: dict[str, Any] = {
            "event": "hub.worker_route.diagnostic",
            "route": route,
            "stage": stage,
            "diag_id": int(diag_id),
            "elapsed_ms": round((time.perf_counter() - started_at) * 1000.0, 3),
            "port": port,
            "client": self.client_address[0] if self.client_address else "",
            "thread": threading.current_thread().name,
        }
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                payload[key] = value
            elif isinstance(value, (list, tuple, set)):
                payload[key] = [str(item) for item in list(value)[:20]]
            elif isinstance(value, dict):
                payload[key] = {str(k): str(v) for k, v in list(value.items())[:20]}
            else:
                payload[key] = str(value)
        try:
            print(f"[hub-worker-route:{port}] {json.dumps(payload, sort_keys=True)}", file=sys.stderr, flush=True)
        except Exception:
            pass

    def _worker_route_enter_or_reject(self, diag_id: int, route: str, started_at: float) -> bool:
        semaphore = getattr(self.server, "worker_route_semaphore", None)
        max_in_flight = int(getattr(self.server, "worker_route_max_in_flight", 0) or 0)
        if semaphore is None or max_in_flight <= 0:
            self._worker_route_diag_step(diag_id, route, "route_gate.disabled", started_at)
            return True

        if semaphore.acquire(blocking=False):
            in_flight = 0
            lock = getattr(self.server, "worker_route_in_flight_lock", None)
            if lock is not None:
                with lock:
                    self.server.worker_route_in_flight = int(getattr(self.server, "worker_route_in_flight", 0) or 0) + 1
                    in_flight = int(self.server.worker_route_in_flight)
            self._worker_route_diag_step(
                diag_id,
                route,
                "route_gate.acquired",
                started_at,
                worker_route_in_flight=in_flight,
                worker_route_max_in_flight=max_in_flight,
            )
            return True

        retry_after = float(getattr(self.server, "worker_route_retry_after_seconds", 1.0) or 1.0)
        self._worker_route_diag_step(
            diag_id,
            route,
            "route_gate.rejected",
            started_at,
            status=503,
            worker_route_max_in_flight=max_in_flight,
            retry_after_seconds=retry_after,
        )
        self._worker_route_diag_step(diag_id, route, "request_body.discard.start", started_at)
        self._discard_request_body()
        self._worker_route_diag_step(
            diag_id,
            route,
            "request_body.discard.done",
            started_at,
            close_connection=bool(getattr(self, "close_connection", False)),
        )
        self._worker_route_diag_step(diag_id, route, "route_gate.reject_sent", started_at, status=503)
        self._send_json(
            {
                "ok": False,
                "error": "Hub worker route overloaded; retry later.",
                "error_type": "hub_worker_route_overloaded",
                "retry_after_seconds": retry_after,
                "worker_route_max_in_flight": max_in_flight,
            },
            status=HTTPStatus.SERVICE_UNAVAILABLE,
        )
        return False

    def _worker_route_exit(self, diag_id: int, route: str, started_at: float) -> None:
        semaphore = getattr(self.server, "worker_route_semaphore", None)
        if semaphore is None:
            return
        in_flight = 0
        lock = getattr(self.server, "worker_route_in_flight_lock", None)
        if lock is not None:
            with lock:
                self.server.worker_route_in_flight = max(0, int(getattr(self.server, "worker_route_in_flight", 0) or 0) - 1)
                in_flight = int(self.server.worker_route_in_flight)
        try:
            semaphore.release()
        except ValueError:
            pass
        self._worker_route_diag_step(diag_id, route, "route_gate.released", started_at, worker_route_in_flight=in_flight)

    def _worker_route_success_payload(self, worker: Any) -> dict[str, Any]:
        worker_data = worker.as_dict()
        capabilities = worker_data.get("capabilities", {}) if isinstance(worker_data.get("capabilities"), dict) else {}
        payload = {
            "ok": True,
            "worker": worker_data,
            "hub": {"status_omitted": True},
            "hub_status_omitted": True,
        }
        for key in (
            "requested_ring",
            "effective_ring",
            "minimum_allowed_ring",
            "allowed_min_ring",
            "ring_admission_status",
            "ring_admission_message",
            "fallback_ring",
        ):
            if key in capabilities:
                payload[key] = capabilities.get(key)
        return payload

    def _discard_request_body(self) -> None:
        transfer_encoding = str(self.headers.get("Transfer-Encoding", "") or "").lower()
        if "chunked" in transfer_encoding:
            self.close_connection = True
            return

        try:
            remaining = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self.close_connection = True
            return

        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 64 * 1024))
            if not chunk:
                self.close_connection = True
                return
            remaining -= len(chunk)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return data

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(
        self,
        payload: bytes,
        *,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(payload)

    def _send_jsonl_payload(self, payload: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        self.wfile.flush()

    def _request_stream_payload(
        self,
        *,
        request_id: str,
        event_index: int,
        event: dict[str, Any],
        record: Any,
    ) -> dict[str, Any]:
        event_type = str(event.get("type") or event.get("event") or "message").strip() or "message"
        stream_event = str(event.get("stream_event") or event.get("stream_event_type") or "").strip()
        event_name = stream_event if event_type == "worker.stream.event" and stream_event else event_type
        payload: dict[str, Any] = {
            "event": event_name,
            "request_event_type": event_type,
            "event_index": int(event_index),
            "request_id": request_id,
            "state": str(event.get("state") or getattr(record, "state", "") or ""),
            "created_at": str(event.get("created_at", "") or ""),
            "payload": dict(event),
        }
        for key in (
            "stream_event",
            "text",
            "delta",
            "content_delta",
            "thinking_delta",
            "message",
            "progress",
            "token_count",
            "worker_node_id",
            "worker_instance_id",
            "lease_id",
        ):
            if key in event:
                payload[key] = event[key]
        return payload

    def _stream_request_events(self, request_id: str, query: dict[str, list[str]]) -> None:
        try:
            next_index = max(0, int(str(query.get("after", ["0"])[0] or "0")))
        except (TypeError, ValueError):
            next_index = 0
        try:
            max_seconds = max(0.1, min(600.0, float(str(query.get("timeout_seconds", ["30"])[0] or "30"))))
        except (TypeError, ValueError):
            max_seconds = 30.0
        try:
            heartbeat_seconds = max(0.25, min(30.0, float(str(query.get("heartbeat_seconds", ["5"])[0] or "5"))))
        except (TypeError, ValueError):
            heartbeat_seconds = 5.0

        self.send_response(int(HTTPStatus.OK))
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        started = time.monotonic()
        terminal_states = {"completed", "failed", "cancelled", "expired"}
        try:
            self._send_jsonl_payload(
                {
                    "event": "open",
                    "request_id": request_id,
                    "transport": "hub-request-jsonl",
                    "after": next_index,
                }
            )
            while True:
                remaining = max_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    self._send_jsonl_payload(
                        {
                            "event": "timeout",
                            "request_id": request_id,
                            "next_event_index": next_index,
                        }
                    )
                    return
                events, record = self.server.dispatcher.wait_for_request_events(
                    request_id,
                    after=next_index,
                    timeout_s=min(heartbeat_seconds, remaining),
                )
                for event in events:
                    self._send_jsonl_payload(
                        self._request_stream_payload(
                            request_id=request_id,
                            event_index=next_index,
                            event=event,
                            record=record,
                        )
                    )
                    next_index += 1
                if str(getattr(record, "state", "")) in terminal_states:
                    self._send_jsonl_payload(
                        {
                            "event": "done",
                            "request_id": request_id,
                            "state": str(getattr(record, "state", "")),
                            "next_event_index": next_index,
                        }
                    )
                    return
                if not events:
                    self._send_jsonl_payload(
                        {
                            "event": "heartbeat",
                            "request_id": request_id,
                            "next_event_index": next_index,
                            "state": str(getattr(record, "state", "")),
                        }
                    )
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


class HubHttpServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: MainComputerConfig, *, verbose: bool = True) -> None:
        super().__init__(server_address, HubServerHandler)
        self.worker_route_diagnostics = str(os.environ.get("HUB_WORKER_ROUTE_DIAGNOSTICS", "0")).strip().lower() in {"1", "true", "yes", "on"}
        self.worker_route_diag_lock = threading.Lock()
        self.worker_route_diag_sequence = 0
        self.worker_route_max_in_flight = max(0, int(os.environ.get("HUB_WORKER_ROUTE_MAX_IN_FLIGHT", "8")))
        self.worker_route_retry_after_seconds = max(0.1, float(os.environ.get("HUB_WORKER_ROUTE_RETRY_AFTER_SECONDS", "1")))
        self.worker_route_semaphore = (
            threading.BoundedSemaphore(self.worker_route_max_in_flight)
            if self.worker_route_max_in_flight > 0
            else None
        )
        self.worker_route_in_flight_lock = threading.Lock()
        self.worker_route_in_flight = 0
        self.ring_admission_audit_diag_lock = threading.Lock()
        self.ring_admission_audit_write_failure_count = 0
        self.ring_admission_audit_write_failure_suppressed_count = 0
        self.ring_admission_audit_last_write_failure: dict[str, Any] | None = None
        self.ring_admission_audit_last_log_monotonic = 0.0
        try:
            self.ring_admission_audit_log_interval_seconds = max(
                0.0,
                float(os.environ.get("HUB_RING_ADMISSION_AUDIT_LOG_INTERVAL_SECONDS", "30")),
            )
        except ValueError:
            self.ring_admission_audit_log_interval_seconds = 30.0
        hub_root = config.hub_root
        if not hub_root.is_absolute():
            hub_root = Path.cwd().resolve() / hub_root
        self.verbose = verbose
        self.config = config
        self.hub_root = hub_root
        self.ring_admission_config = load_ring_admission_config(getattr(config, "hub_ring_config_path", None))
        self.registry = HubRegistry(
            hub_root,
            allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
        )
        self.energy_ledger = EnergyCreditLedger(hub_root / "energy_credits")
        self.credit_ledger = HubCreditLedger(hub_root / "compute_credits")
        self.credit_indexer = HubCreditIndexer(self.credit_ledger)
        self.credit_bridge_completion = HubCreditBridgeCompletionService(self.credit_ledger, config)
        self.bridge_backend = build_hub_bridge_backend(
            backend_name=config.hub_bridge_backend,
            repo_root=Path.cwd().resolve(),
            dev_chain_deployment_path=config.hub_dev_chain_deployment_path,
            contracts_path=config.hub_contracts_path,
            network_key=config.hub_network,
            chain_rpc_url=config.chain_rpc_url,
            allow_missing_bridge_signer=config.hub_allow_missing_bridge_signer,
            enable_smoke_bridge=config.hub_enable_smoke_bridge,
        )
        self.multisession_key_store_path = hub_root / "compute_credits" / "multisession_keys.json"
        self.multisession_key_store = None
        self.multisession_key_store_lock = threading.Lock()
        self.dispatcher = HubDispatcher(
            self.registry,
            self.energy_ledger,
            timeout_s=config.hub_timeout_s,
            allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
            credit_ledger=self.credit_ledger,
            default_credits_per_request=config.hub_credits_per_request,
        )


    def note_ring_admission_audit_write_failure(self, event: dict[str, Any], exc: BaseException) -> None:
        """Record and rate-limit ring-admission audit side-write failures.

        Ring admission itself is still handled by the caller.  The audit write is
        best-effort, but failures must be diagnosable and visible in status.
        """
        clean_event = event if isinstance(event, dict) else {}
        detail: dict[str, Any] = {
            "error_type": type(exc).__name__,
            "error": str(exc),
            "node_id": str(clean_event.get("node_id") or ""),
            "worker_instance_id": str(clean_event.get("worker_instance_id") or ""),
            "wallet_address": str(clean_event.get("wallet_address") or ""),
            "requested_ring": clean_event.get("requested_ring"),
            "minimum_allowed_ring": clean_event.get("minimum_allowed_ring"),
            "fallback_ring": clean_event.get("fallback_ring"),
            "ring_config_hash": str(clean_event.get("ring_config_hash") or ""),
            "failure_count": 0,
            "suppressed_since_last_log": 0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        now = time.monotonic()
        should_log = False
        suppressed_since_last = 0
        with self.ring_admission_audit_diag_lock:
            self.ring_admission_audit_write_failure_count += 1
            detail["failure_count"] = self.ring_admission_audit_write_failure_count
            interval = self.ring_admission_audit_log_interval_seconds
            should_log = bool(self.verbose) and (
                self.ring_admission_audit_last_log_monotonic <= 0.0
                or interval <= 0.0
                or now - self.ring_admission_audit_last_log_monotonic >= interval
            )
            if should_log:
                suppressed_since_last = self.ring_admission_audit_write_failure_suppressed_count
                self.ring_admission_audit_write_failure_suppressed_count = 0
                self.ring_admission_audit_last_log_monotonic = now
            else:
                self.ring_admission_audit_write_failure_suppressed_count += 1
            detail["suppressed_since_last_log"] = suppressed_since_last
            self.ring_admission_audit_last_write_failure = dict(detail)

        if should_log:
            context = " ".join(
                [
                    f"error_type={detail['error_type']}",
                    f"error={detail['error']!r}",
                    f"node_id={detail['node_id']!r}",
                    f"worker_instance_id={detail['worker_instance_id']!r}",
                    f"wallet_address={detail['wallet_address']!r}",
                    f"requested_ring={detail['requested_ring']!r}",
                    f"minimum_allowed_ring={detail['minimum_allowed_ring']!r}",
                    f"fallback_ring={detail['fallback_ring']!r}",
                    f"failure_count={detail['failure_count']}",
                    f"suppressed_since_last_log={suppressed_since_last}",
                ]
            )
            print(f"hub ring admission audit write failed: {context}", file=sys.stderr, flush=True)

    def ring_admission_audit_diagnostics(self) -> dict[str, Any]:
        with self.ring_admission_audit_diag_lock:
            return {
                "write_failure_count": self.ring_admission_audit_write_failure_count,
                "suppressed_write_failure_count": self.ring_admission_audit_write_failure_suppressed_count,
                "last_write_failure": dict(self.ring_admission_audit_last_write_failure)
                if self.ring_admission_audit_last_write_failure
                else None,
            }



def serving_hub_identity_for_server(server: Any) -> dict[str, Any]:
    """Return the concrete Hub identity that handled the current request.

    Stable multi-Hub deployments expose a shared entry URL such as
    ``testnet-hub.greatlibrary.io``.  The admin UI should still show the concrete
    Hub process that served the request, using the topology hub id
    (``testnet-hub2``) rather than an implementation address.
    """

    node = getattr(server, "stable_hub_node", None)
    if node is not None:
        hub_id = str(getattr(node, "hub_id", "") or "").strip()
        return {
            "hub_id": hub_id or "main-computer-hub",
            "display_name": hub_id or "main-computer-hub",
            "hub_url": str(getattr(node, "hub_url", "") or "").strip(),
            "public_url": str(getattr(node, "public_url", "") or "").strip(),
            "roles": list(getattr(node, "roles", ()) or ()),
            "source": "stable_topology",
        }

    config = getattr(server, "config", None)
    configured_id = str(getattr(config, "hub_id", "") or "").strip()
    fallback_id = configured_id or "main-computer-hub"
    return {
        "hub_id": fallback_id,
        "display_name": fallback_id,
        "hub_url": str(getattr(config, "hub_url", "") or "").strip(),
        "public_url": str(getattr(config, "hub_url", "") or "").strip(),
        "roles": [],
        "source": "local_hub",
    }


class HubServerHandler(_JsonHandler):
    server: HubHttpServer

    def _load_multisession_key_store_unlocked(self) -> dict[str, Any]:
        store = getattr(self.server, "multisession_key_store", None)
        if store is not None and hasattr(store, "load"):
            data = store.load()
        else:
            path = self.server.multisession_key_store_path
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        keys = data.get("keys")
        if not isinstance(keys, dict):
            keys = {}
        data["keys"] = keys
        data.setdefault("version", "main-computer-multisession-keys-v1")
        return data

    def _save_multisession_key_store_unlocked(self, data: dict[str, Any]) -> None:
        store = getattr(self.server, "multisession_key_store", None)
        if store is not None and hasattr(store, "save"):
            store.save(data)
            return
        path = self.server.multisession_key_store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _active_multisession_key_for_wallet_unlocked(
        self,
        data: dict[str, Any],
        *,
        wallet_address: str,
    ) -> dict[str, Any] | None:
        for record in data.get("keys", {}).values():
            if not isinstance(record, dict):
                continue
            if (
                record.get("status") == "active"
                and str(record.get("wallet_address", "")).lower() == wallet_address
            ):
                return dict(record)
        return None

    def _multisession_authorization_from_body(self, body: dict[str, Any]) -> dict[str, Any]:
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        for key in ("multisession_authorization", "payment_authorization"):
            value = body.get(key)
            if isinstance(value, dict):
                return dict(value)
            value = metadata.get(key) if isinstance(metadata, dict) else None
            if isinstance(value, dict):
                return dict(value)
        return {}

    def _validate_multisession_authorization(
        self,
        authorization: dict[str, Any],
        *,
        required_wallet_address: str = "",
        required_chain_id: str = "",
        required_credit_wei: int = 0,
    ) -> dict[str, Any]:
        if not isinstance(authorization, dict) or not authorization:
            raise HubCreditAuthorizationError("A multi-session key authorization is required.")

        kind = str(authorization.get("kind") or "multisession_key").strip().lower()
        if kind != "multisession_key":
            raise HubCreditAuthorizationError("Authorization kind must be multisession_key.")

        wallet_address = normalize_address(authorization.get("wallet_address"))
        if required_wallet_address and normalize_address(required_wallet_address) != wallet_address:
            raise HubCreditAuthorizationError("The multi-session key wallet does not match the required wallet.")

        key_id = str(
            authorization.get("multisession_key_id")
            or authorization.get("key_id")
            or ""
        ).strip()
        if not key_id:
            raise HubCreditAuthorizationError("A multi-session key id is required.")

        with self.server.multisession_key_store_lock:
            data = self._load_multisession_key_store_unlocked()
            record = data.get("keys", {}).get(key_id)
            if not isinstance(record, dict) or record.get("status") != "active":
                raise HubCreditAuthorizationError("The multi-session key is not active.")
            record = dict(record)

        record_wallet = normalize_address(record.get("wallet_address"))
        if record_wallet != wallet_address:
            raise HubCreditAuthorizationError("The multi-session key does not belong to the requested wallet.")

        record_chain_id = normalize_chain_id(record.get("chain_id"))
        requested_chain_id = normalize_chain_id(authorization.get("chain_id") or required_chain_id or record_chain_id)
        if requested_chain_id and record_chain_id and requested_chain_id != record_chain_id:
            raise HubCreditAuthorizationError("The multi-session key chain id does not match this request.")
        if requested_chain_id and requested_chain_id != HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID:
            raise HubCreditAuthorizationError("This Hub only accepts local dev-chain multi-session keys for wallet-authenticated lab work.")

        max_authorized_credit_wei = _hub_as_int(
            authorization.get("max_authorized_credit_wei"),
            0,
            minimum=0,
        )
        if max_authorized_credit_wei <= 0:
            max_authorized_credits = _hub_as_int(
                authorization.get("max_authorized_credits", authorization.get("max_credits")),
                0,
                minimum=0,
            )
            if max_authorized_credits > 0:
                max_authorized_credit_wei = max_authorized_credits * CREDIT_WEI_PER_CREDIT
        if required_credit_wei > 0 and max_authorized_credit_wei > 0 and max_authorized_credit_wei < required_credit_wei:
            raise HubCreditAuthorizationError("The multi-session key authorization is below the requested credit spend.")

        account_id = wallet_account_id(wallet_address)
        return {
            "kind": "multisession_key",
            "wallet_address": wallet_address,
            "account_id": account_id,
            "multisession_key_id": key_id,
            "chain_id": requested_chain_id or record_chain_id,
            "record": record,
            "max_authorized_credit_wei": str(max_authorized_credit_wei),
        }

    def _apply_request_multisession_authorization(
        self,
        *,
        body: dict[str, Any],
        metadata: dict[str, Any],
        required: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        authorization = self._multisession_authorization_from_body(body)
        if not authorization:
            if required:
                raise HubCreditAuthorizationError("Wallet-backed worker-pull requests require a multi-session key authorization.")
            return body, metadata

        requested_credits = _hub_as_int(
            body.get("max_credits", metadata.get("max_credits", body.get("max_price_credits", metadata.get("max_price_credits", 0)))),
            0,
            minimum=0,
        )
        verified = self._validate_multisession_authorization(
            authorization,
            required_chain_id=str(body.get("chain_id") or metadata.get("chain_id") or ""),
            required_credit_wei=requested_credits * CREDIT_WEI_PER_CREDIT if requested_credits > 0 else 0,
        )

        clean_body = dict(body)
        clean_metadata = dict(metadata)
        clean_body["account_id"] = verified["account_id"]
        clean_metadata["account_id"] = verified["account_id"]
        clean_metadata["wallet_address"] = verified["wallet_address"]
        clean_metadata["multisession_key_id"] = verified["multisession_key_id"]
        clean_metadata["multisession_key_authorized"] = True
        clean_metadata["auth_mode"] = "multisession-wallet"
        if verified.get("chain_id"):
            clean_metadata["chain_id"] = verified["chain_id"]
        clean_body["metadata"] = clean_metadata
        return clean_body, clean_metadata

    def _registered_worker_wallet(self, worker: Any) -> str:
        if worker is None:
            return ""
        capabilities = getattr(worker, "capabilities", None)
        if not isinstance(capabilities, dict):
            try:
                payload = worker.as_dict()
            except Exception:
                payload = {}
            capabilities = payload.get("capabilities") if isinstance(payload, dict) else {}
        try:
            return normalize_address(
                (capabilities or {}).get("wallet_address")
                or (capabilities or {}).get("worker_wallet_address")
                or ((capabilities or {}).get("worker_registration", {}) or {}).get("wallet_address")
            )
        except Exception:
            return ""

    def _authorize_worker_route(
        self,
        *,
        body: dict[str, Any],
        worker_id: str,
        current_worker: Any = None,
        registration: bool = False,
    ) -> dict[str, Any]:
        authorization = self._multisession_authorization_from_body(body)
        require_auth = bool(getattr(self.server.config, "hub_require_multisession_auth", False))
        if not authorization:
            if require_auth:
                raise HubCreditAuthorizationError("Worker routes require a multi-session key authorization.")
            return {}

        verified = self._validate_multisession_authorization(
            authorization,
            required_chain_id=str(body.get("chain_id") or ""),
        )
        if not registration:
            if current_worker is None:
                raise HubCreditAuthorizationError("Worker must be registered before authenticated heartbeat, poll, or result submission.")
            registered_wallet = self._registered_worker_wallet(current_worker)
            if registered_wallet and registered_wallet != verified["wallet_address"]:
                raise HubCreditAuthorizationError("The multi-session key wallet does not match the registered worker wallet.")

        return verified

    def _wallet_credit_balance_payload(self, wallet_address: str) -> dict[str, Any]:
        normalized_wallet = normalize_address(wallet_address)
        account_id = wallet_account_id(normalized_wallet)
        account = self.server.credit_ledger.get_account(account_id)
        ledger_status = self.server.credit_ledger.status(recent_limit=10)
        return {
            "ok": True,
            "wallet_address": normalized_wallet,
            "account_id": account_id,
            "account": account.as_dict(),
            "unit": ledger_status["unit"],
            "funding_model": "hub_credit_bridge_escrow_wallet_v1",
        }

    def _with_hub_bridge_backend_deposit_metadata(self, *, deposit_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        backend = self.server.bridge_backend
        if getattr(backend, "name", "mock-chain") != "dev-chain":
            return metadata
        if metadata.get("dev_chain"):
            # Compatibility for older harness-side movement metadata. New runs
            # should leave dev-chain movement ownership inside the Hub backend.
            return metadata
        if not hasattr(self.server.credit_ledger, "bridge_deposit_status"):
            raise HubBridgeBackendError("credit ledger cannot expose pending bridge deposit payloads.")
        payload = self.server.credit_ledger.bridge_deposit_status(deposit_id).get("deposit", {})
        if not isinstance(payload, dict) or not payload:
            raise HubBridgeBackendError(f"Unknown bridge deposit: {deposit_id}")
        if str(payload.get("status", "")) == "confirmed":
            return metadata
        backend_metadata = backend.deposit_confirmation_metadata(payload)
        return {**metadata, **backend_metadata}

    def _with_hub_bridge_backend_payout_metadata(self, *, payout_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        backend = self.server.bridge_backend
        if getattr(backend, "name", "mock-chain") != "dev-chain":
            return metadata
        if metadata.get("dev_chain"):
            return metadata
        if not hasattr(self.server.credit_ledger, "bridge_payout_status"):
            raise HubBridgeBackendError("credit ledger cannot expose pending bridge payout payloads.")
        payload = self.server.credit_ledger.bridge_payout_status(payout_id).get("payout", {})
        if not isinstance(payload, dict) or not payload:
            raise HubBridgeBackendError(f"Unknown bridge payout: {payout_id}")
        if str(payload.get("status", "")) == "confirmed":
            return metadata
        backend_metadata = backend.payout_confirmation_metadata(payload)
        return {**metadata, **backend_metadata}

    def _worker_ring_from_payload(self, worker: dict[str, Any]) -> str:
        capabilities = worker.get("capabilities") if isinstance(worker.get("capabilities"), dict) else {}
        ring = str(worker.get("assigned_ring") or capabilities.get("assigned_ring") or capabilities.get("requested_ring") or "").strip()
        return ring

    def _worker_is_available_for_pool(self, worker: dict[str, Any]) -> bool:
        try:
            active_requests = int(worker.get("active_requests", 0) or 0)
            max_concurrency = int(worker.get("max_concurrency", 1) or 1)
        except (TypeError, ValueError):
            active_requests = 0
            max_concurrency = 1
        return (
            str(worker.get("status", "available")).lower() in {"available", "configured"}
            and not bool(worker.get("stale", False))
            and active_requests < max(1, max_concurrency)
        )

    def _worker_pool_summary(self, *, assigned_ring: str) -> dict[str, Any]:
        status = self.server.registry.status()
        workers = [worker for worker in status.get("workers", []) if isinstance(worker, dict)]
        ring_workers = [worker for worker in workers if self._worker_ring_from_payload(worker) == str(assigned_ring)]
        return {
            "worker_count": int(status.get("worker_count", len(workers)) or 0),
            "available_worker_count": int(status.get("available_worker_count", 0) or 0),
            "stale_worker_count": int(status.get("stale_worker_count", 0) or 0),
            "ring": str(assigned_ring),
            "ring_worker_count": len(ring_workers),
            "ring_available_worker_count": len([worker for worker in ring_workers if self._worker_is_available_for_pool(worker)]),
        }

    def _normalize_worker_wallet_address(self, value: Any) -> str:
        if value is None:
            return ""
        try:
            return normalize_address(str(value))
        except ValueError:
            return str(value or "").strip().lower()

    def _wallet_address_from_worker_payload(self, body: dict[str, Any], capabilities: dict[str, Any] | None = None) -> str:
        raw_wallet_address = (
            body.get("wallet_address")
            or body.get("worker_wallet_address")
            or body.get("payout_wallet_address")
        )
        if raw_wallet_address is None and isinstance(body.get("wallet"), dict):
            raw_wallet_address = body.get("wallet", {}).get("address")
        if raw_wallet_address is None and isinstance(capabilities, dict):
            raw_wallet_address = (
                capabilities.get("wallet_address")
                or capabilities.get("worker_wallet_address")
                or capabilities.get("payout_wallet_address")
            )
        return self._normalize_worker_wallet_address(raw_wallet_address)

    def _payload_has_requested_ring(self, body: dict[str, Any], capabilities: dict[str, Any] | None = None) -> bool:
        for ring_key in ("assigned_ring", "ring", "requested_ring"):
            if body.get(ring_key) is not None:
                return True
        if isinstance(capabilities, dict):
            for ring_key in ("effective_ring", "assigned_ring", "ring", "requested_ring"):
                if capabilities.get(ring_key) is not None:
                    return True
        return False

    def _requested_ring_from_worker_payload(
        self,
        body: dict[str, Any],
        capabilities: dict[str, Any] | None = None,
        *,
        default: int | None = 3,
    ) -> int:
        for ring_key in ("assigned_ring", "ring", "requested_ring"):
            if body.get(ring_key) is not None:
                return normalize_requested_ring(body.get(ring_key), default=default, field_name=ring_key)
        if isinstance(capabilities, dict):
            for ring_key in ("effective_ring", "assigned_ring", "ring", "requested_ring"):
                if capabilities.get(ring_key) is not None:
                    return normalize_requested_ring(capabilities.get(ring_key), default=default, field_name=f"capabilities.{ring_key}")
        return normalize_requested_ring(None, default=default, field_name="requested_ring")

    def _current_worker_effective_ring(self, worker: HubWorker | None) -> int | None:
        if worker is None:
            return None
        capabilities = worker.capabilities if isinstance(worker.capabilities, dict) else {}
        for ring_key in ("effective_ring", "assigned_ring", "requested_ring", "ring"):
            if capabilities.get(ring_key) is not None:
                return normalize_requested_ring(capabilities.get(ring_key), default=None, field_name=f"current.{ring_key}")
        offer = worker.offer if isinstance(worker.offer, dict) else {}
        if offer.get("assigned_ring") is not None:
            return normalize_requested_ring(offer.get("assigned_ring"), default=None, field_name="current.offer.assigned_ring")
        return None

    def _ring_admission_rejection_payload(
        self,
        *,
        decision: RingAdmissionDecision,
        wallet_address: str,
        node_id: str,
        error: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "ok": False,
            **decision.as_response_fields(),
        }
        if error:
            payload["error"] = error
        payload.setdefault("error", decision.error or "ring_not_allowed")
        if wallet_address:
            payload["wallet_address"] = wallet_address
        if node_id:
            payload["node_id"] = _clean_node_id(node_id, default=node_id)
        return payload

    def _record_ring_admission_rejection(
        self,
        *,
        wallet_address: str,
        node_id: str,
        worker_instance_id: str = "",
        requested_ring: int,
        minimum_allowed_ring: int,
        fallback_ring: int | None,
        error: str,
        message: str,
    ) -> None:
        event = {
            "event_type": "ring_admission_rejected",
            "wallet_address": wallet_address,
            "node_id": _clean_node_id(node_id, default=node_id),
            "worker_instance_id": _clean_node_id(worker_instance_id, default="") if worker_instance_id else "",
            "requested_ring": int(requested_ring),
            "minimum_allowed_ring": int(minimum_allowed_ring),
            "allowed_min_ring": int(minimum_allowed_ring),
            "fallback_ring": int(fallback_ring) if fallback_ring is not None else None,
            "error": str(error or "ring_not_allowed"),
            "message": str(message or ""),
            "ring_config_hash": self.server.ring_admission_config.config_hash(),
        }
        try:
            self.server.registry.record_ring_admission_rejection(event)
        except Exception as exc:
            self.server.note_ring_admission_audit_write_failure(event, exc)

    def _evaluate_ring_registration(
        self,
        *,
        body: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> tuple[RingAdmissionDecision, str]:
        wallet_address = self._wallet_address_from_worker_payload(body, capabilities)
        requested_ring = self._requested_ring_from_worker_payload(body, capabilities, default=3)
        decision = self.server.ring_admission_config.evaluate(
            wallet_address=wallet_address,
            requested_ring=requested_ring,
        )
        return decision, wallet_address

    def _apply_accepted_ring_admission(
        self,
        *,
        capabilities: dict[str, Any],
        decision: RingAdmissionDecision,
        wallet_address: str,
    ) -> None:
        effective_ring = int(decision.effective_ring if decision.effective_ring is not None else decision.requested_ring)
        capabilities["requested_ring"] = int(decision.requested_ring)
        capabilities["assigned_ring"] = effective_ring
        capabilities["effective_ring"] = effective_ring
        capabilities["minimum_allowed_ring"] = int(decision.minimum_allowed_ring)
        capabilities["allowed_min_ring"] = int(decision.minimum_allowed_ring)
        capabilities["ring_admission_status"] = "accepted"
        capabilities["ring_admission_message"] = decision.message
        if wallet_address:
            capabilities["wallet_address"] = wallet_address

    def _preserve_registered_ring_capabilities(
        self,
        *,
        capabilities: dict[str, Any],
        current_worker: HubWorker | None,
    ) -> None:
        if current_worker is None:
            return
        current_capabilities = current_worker.capabilities if isinstance(current_worker.capabilities, dict) else {}
        for key in (
            "requested_ring",
            "assigned_ring",
            "effective_ring",
            "minimum_allowed_ring",
            "allowed_min_ring",
            "ring_admission_status",
            "ring_admission_message",
            "wallet_address",
        ):
            if key in current_capabilities and key not in capabilities:
                capabilities[key] = current_capabilities[key]

    def _heartbeat_ring_change_rejection_payload(
        self,
        *,
        requested_ring: int,
        current_effective_ring: int | None,
        minimum_allowed_ring: int,
        wallet_address: str,
        node_id: str,
    ) -> dict[str, Any]:
        message = (
            "Heartbeat cannot change worker ring; re-register to change rings."
            if current_effective_ring is None
            else f"Heartbeat cannot change worker ring from {current_effective_ring} to {requested_ring}; re-register to change rings."
        )
        payload: dict[str, Any] = {
            "ok": False,
            "error": "ring_change_requires_reregister",
            "requested_ring": int(requested_ring),
            "effective_ring": current_effective_ring,
            "current_effective_ring": current_effective_ring,
            "minimum_allowed_ring": int(minimum_allowed_ring),
            "allowed_min_ring": int(minimum_allowed_ring),
            "fallback_ring": current_effective_ring,
            "ring_admission_status": "rejected",
            "ring_admission_message": message,
            "message": message,
        }
        if wallet_address:
            payload["wallet_address"] = wallet_address
        if node_id:
            payload["node_id"] = _clean_node_id(node_id, default=node_id)
        return payload

    def _verify_worker_start_authorization(self, *, body: dict[str, Any], worker_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        authorization = self._multisession_authorization_from_body(body)
        if not authorization:
            raise HubCreditAuthorizationError(
                "Worker Hub connection requires a saved multi-session key authorization for this network's Hub."
            )
        try:
            worker_authorization = self._validate_multisession_authorization(
                authorization,
                required_wallet_address=str(body.get("credit_wallet") or body.get("wallet_address") or ""),
                required_chain_id=str(body.get("chain_id") or ""),
            )
        except HubCreditAuthorizationError as exc:
            message = str(exc)
            if "not active" in message:
                raise HubCreditAuthorizationError(
                    "The saved multi-session key is not active on this Hub. "
                    "Request a new multi-session key for this network's Hub before connecting."
                ) from exc
            raise

        wallet_address = normalize_address(worker_authorization["wallet_address"])
        body_wallet = body.get("wallet_address")
        if body_wallet and normalize_address(body_wallet) != wallet_address:
            raise ValueError("worker registration wallet does not match the multi-session key wallet.")
        credit_wallet = normalize_address(body.get("credit_wallet") or wallet_address)
        if credit_wallet != wallet_address:
            raise ValueError("worker registration credit wallet must match the multi-session key wallet.")

        network = str(body.get("network") or "").strip().lower()
        if network not in {"mainnet", "testnet", "test", "dev"}:
            raise ValueError(f"bad worker registration network: {network!r}")

        requested_ring = str(body.get("requested_ring") or "").strip()
        if requested_ring not in {"0", "1", "2", "3"}:
            raise ValueError("worker registration requested_ring must be 0, 1, 2, or 3.")

        hub_url = str(body.get("hub_url") or "").strip().rstrip("/")
        if not hub_url:
            raise ValueError("worker registration must include hub_url.")

        chain_id = str(body.get("chain_id") or worker_authorization.get("chain_id") or "").strip()
        if not chain_id:
            raise ValueError("worker registration must include chain_id.")

        worker_node_id = _clean_node_id(str(worker_payload.get("node_id") or ""), default="")
        if not worker_node_id:
            raise ValueError("worker.node_id is required.")

        return {
            "ok": True,
            "wallet_address": wallet_address,
            "credit_wallet": credit_wallet,
            "network": network,
            "requested_ring": requested_ring,
            "hub_url": hub_url,
            "chain_id": chain_id,
            "worker_node_id": worker_node_id,
            "multisession_key_id": worker_authorization["multisession_key_id"],
            "worker_authorization": worker_authorization,
        }, worker_authorization

    def _handle_worker_start_registration(self, body: dict[str, Any]) -> dict[str, Any]:
        worker_payload = body.get("worker")
        if not isinstance(worker_payload, dict):
            raise ValueError("worker object is required.")

        verification, worker_authorization = self._verify_worker_start_authorization(
            body=body,
            worker_payload=worker_payload,
        )
        assigned_ring = str(verification["requested_ring"])
        ring_decision = self.server.ring_admission_config.evaluate(
            wallet_address=verification["wallet_address"],
            requested_ring=assigned_ring,
        )
        if not ring_decision.ok:
            worker_node_id = str(worker_payload.get("node_id", ""))
            worker_instance_id_for_audit = _clean_node_id(
                str(worker_payload.get("worker_instance_id") or worker_payload.get("connection_id") or worker_node_id),
                default=worker_node_id,
            )
            self._record_ring_admission_rejection(
                wallet_address=verification["wallet_address"],
                node_id=worker_node_id,
                worker_instance_id=worker_instance_id_for_audit,
                requested_ring=ring_decision.requested_ring,
                minimum_allowed_ring=ring_decision.minimum_allowed_ring,
                fallback_ring=ring_decision.fallback_ring,
                error=ring_decision.error or "ring_not_allowed",
                message=ring_decision.message,
            )
            return self._ring_admission_rejection_payload(
                decision=ring_decision,
                wallet_address=verification["wallet_address"],
                node_id=worker_node_id,
            )
        worker_instance_id = _clean_node_id(
            str(worker_payload.get("worker_instance_id") or worker_payload.get("connection_id") or verification["worker_node_id"]),
            default=verification["worker_node_id"],
        )
        ring_policy = {"0": "operator", "1": "protected", "2": "public", "3": "public-untrusted"}[assigned_ring]
        pricing_policy = f"{ring_policy}-{verification['network']}"

        capabilities = dict(worker_payload.get("capabilities", {})) if isinstance(worker_payload.get("capabilities"), dict) else {}
        pricing = dict(worker_payload.get("pricing", {})) if isinstance(worker_payload.get("pricing"), dict) else {}
        execution = dict(worker_payload.get("execution", {})) if isinstance(worker_payload.get("execution"), dict) else {}
        if pricing:
            capabilities["pricing"] = pricing
        if execution:
            capabilities["execution"] = execution
        capabilities.update(
            {
                "worker_registration_authorized": True,
                "worker_registration": {
                    "network": verification["network"],
                    "requested_ring": verification["requested_ring"],
                    "assigned_ring": assigned_ring,
                    "wallet_address": verification["wallet_address"],
                    "credit_wallet": verification["credit_wallet"],
                    "hub_url": verification["hub_url"],
                    "chain_id": verification["chain_id"],
                    "worker_node_id": verification["worker_node_id"],
                    "worker_instance_id": worker_instance_id,
                    "multisession_key_id": verification["multisession_key_id"],
                },
                "requested_ring": int(ring_decision.requested_ring),
                "assigned_ring": int(ring_decision.effective_ring if ring_decision.effective_ring is not None else ring_decision.requested_ring),
                "effective_ring": int(ring_decision.effective_ring if ring_decision.effective_ring is not None else ring_decision.requested_ring),
                "minimum_allowed_ring": int(ring_decision.minimum_allowed_ring),
                "allowed_min_ring": int(ring_decision.minimum_allowed_ring),
                "ring_admission_status": "accepted",
                "ring_admission_message": ring_decision.message,
                "wallet_address": verification["wallet_address"],
                "credit_wallet": verification["credit_wallet"],
                "pricing_policy": pricing_policy,
                "worker_instance_id": worker_instance_id,
            }
        )
        if worker_authorization:
            capabilities.update(
                {
                    "multisession_key_id": worker_authorization["multisession_key_id"],
                    "multisession_key_authorized": True,
                    "auth_mode": "multisession-wallet",
                }
            )
            if worker_authorization.get("chain_id"):
                capabilities["chain_id"] = worker_authorization["chain_id"]

        raw_price = worker_payload.get("credits_per_request")
        if raw_price is None and pricing:
            raw_price = pricing.get("credits_per_request")
        worker = self.server.registry.register_worker(
            node_id=str(worker_payload.get("node_id", "")),
            endpoint=str(worker_payload.get("endpoint", "")),
            model=str(worker_payload.get("model", "")),
            models=[str(item) for item in worker_payload.get("models", [])] if isinstance(worker_payload.get("models"), list) else None,
            capabilities=capabilities,
            credits_per_request=raw_price if raw_price is not None else self.server.config.hub_credits_per_request,
            settlement_precision_places=worker_payload.get("settlement_precision_places"),
            queue_depth=int(worker_payload.get("queue_depth", 0) or 0),
            active_requests=int(worker_payload.get("active_requests", 0) or 0),
            max_concurrency=int(worker_payload.get("max_concurrency", 1) or 1),
            worker_instance_id=worker_instance_id,
        )
        self.server.energy_ledger.register_node(worker.node_id, "gpu-worker", worker.endpoint)
        worker_data = worker.as_dict()
        worker_data.update(
            {
                "worker_id": worker.worker_instance_id or worker.node_id,
                "worker_instance_id": worker.worker_instance_id or worker.node_id,
                "worker_node_id": worker.node_id,
                "wallet_address": verification["wallet_address"],
                "credit_wallet": verification["credit_wallet"],
                "network": verification["network"],
                "requested_ring": int(ring_decision.requested_ring),
                "assigned_ring": int(ring_decision.effective_ring if ring_decision.effective_ring is not None else ring_decision.requested_ring),
                "effective_ring": int(ring_decision.effective_ring if ring_decision.effective_ring is not None else ring_decision.requested_ring),
                "minimum_allowed_ring": int(ring_decision.minimum_allowed_ring),
                "allowed_min_ring": int(ring_decision.minimum_allowed_ring),
                "ring_admission_status": "accepted",
                "ring_admission_message": ring_decision.message,
                "pricing_policy": pricing_policy,
                "status": worker_data.get("status", "available"),
                "lease_expires_at": worker_data.get("lease_expires_at", ""),
            }
        )
        if worker_authorization:
            worker_data.update(
                {
                    "multisession_key_id": worker_authorization["multisession_key_id"],
                    "multisession_key_authorized": True,
                    "auth_mode": "multisession-wallet",
                }
            )
        pool = self._worker_pool_summary(assigned_ring=assigned_ring)
        return {
            "ok": True,
            "worker": worker_data,
            "worker_id": worker.node_id,
            "wallet_address": verification["wallet_address"],
            "credit_wallet": verification["credit_wallet"],
            "network": verification["network"],
            "requested_ring": int(ring_decision.requested_ring),
            "assigned_ring": int(ring_decision.effective_ring if ring_decision.effective_ring is not None else ring_decision.requested_ring),
            "effective_ring": int(ring_decision.effective_ring if ring_decision.effective_ring is not None else ring_decision.requested_ring),
            "minimum_allowed_ring": int(ring_decision.minimum_allowed_ring),
            "allowed_min_ring": int(ring_decision.minimum_allowed_ring),
            "ring_admission_status": "accepted",
            "ring_admission_message": ring_decision.message,
            "pricing_policy": pricing_policy,
            "status": "registered",
            "verification": verification,
            "pool": pool,
            "hub": self.server.registry.status(),
        }

    def _handle_multisession_key_request(self, body: dict[str, Any]) -> dict[str, Any]:
        signed_request = body.get("signed_request")
        if not isinstance(signed_request, dict):
            raise ValueError("signed_request object is required.")

        verification = verify_personal_sign_blob(
            signed_request,
            expected_chain_id=HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID,
            max_age_minutes=HUB_MULTISESSION_KEY_MAX_AGE_MINUTES,
        )
        wallet_address = str(verification["wallet_address"]).lower()

        now = _utc_now()
        key_seed = {
            "wallet_address": wallet_address,
            "request_id": verification.get("request_id", ""),
            "purpose": "multisession_key_issuance_v1",
        }
        key_id = stable_id("msk", key_seed, length=24)

        with self.server.multisession_key_store_lock:
            data = self._load_multisession_key_store_unlocked()
            existing = data["keys"].get(key_id)
            if isinstance(existing, dict) and existing.get("status") == "active":
                key_payload = dict(existing)
                idempotent = True
            else:
                active_existing = self._active_multisession_key_for_wallet_unlocked(
                    data,
                    wallet_address=wallet_address,
                )
                if active_existing:
                    key_payload = active_existing
                    idempotent = True
                else:
                    key_payload = {
                        "id": key_id,
                        "status": "active",
                        "created_at": now,
                        "revoked_at": "",
                        "wallet_address": wallet_address,
                        "chain_id": verification.get("chain_id", ""),
                        "request_id": verification.get("request_id", ""),
                        "origin": verification.get("origin", ""),
                    }
                    data["keys"][key_id] = key_payload
                    self._save_multisession_key_store_unlocked(data)
                    idempotent = False

        public_verification = dict(verification)
        public_verification.pop("signature", None)
        public_verification.pop("message", None)

        return {
            "ok": True,
            "idempotent": idempotent,
            "verification": public_verification,
            "key": key_payload,
        }

    def _handle_multisession_key_revoke(self, body: dict[str, Any]) -> dict[str, Any]:
        key_id = str(body.get("key_id") or body.get("multisession_key_id") or "").strip()
        if not key_id:
            raise ValueError("key_id is required.")
        wallet_address = normalize_address(body.get("wallet_address"))
        now = _utc_now()

        with self.server.multisession_key_store_lock:
            data = self._load_multisession_key_store_unlocked()
            record = data.get("keys", {}).get(key_id)
            if not isinstance(record, dict):
                raise ValueError("The multi-session key is not active.")
            if str(record.get("wallet_address") or "").lower() != wallet_address:
                raise ValueError("The multi-session key does not belong to the requested wallet.")
            if record.get("status") != "active":
                revoked_record = dict(record)
                return {
                    "ok": True,
                    "revoked": False,
                    "already_inactive": True,
                    "status": str(record.get("status") or ""),
                    "key": {
                        "status": str(revoked_record.get("status") or ""),
                        "wallet_address": str(revoked_record.get("wallet_address") or ""),
                        "revoked_at": str(revoked_record.get("revoked_at") or ""),
                        "key_redacted": True,
                    },
                }
            record = dict(record)
            record["status"] = "revoked"
            record["revoked_at"] = now
            record["updated_at"] = now
            if body.get("reason"):
                record["revocation_reason"] = str(body.get("reason") or "")
            data["keys"][key_id] = record
            self._save_multisession_key_store_unlocked(data)

        return {
            "ok": True,
            "revoked": True,
            "key": {
                "status": "revoked",
                "wallet_address": wallet_address,
                "revoked_at": now,
                "key_redacted": True,
            },
        }

    def _handle_multisession_key_validate(self, body: dict[str, Any]) -> dict[str, Any]:
        """Return Hub-side readiness for a locally cached multi-session key.

        This endpoint is diagnostic/readiness-only: invalid keys and insufficient
        credits return HTTP 200 with valid/ready flags instead of spending,
        holding, or revealing any private key material.
        """

        authorization = body.get("payment_authorization")
        if not isinstance(authorization, dict):
            authorization = body.get("metadata", {}).get("payment_authorization") if isinstance(body.get("metadata"), dict) else {}
        if not isinstance(authorization, dict):
            authorization = {}

        wallet_value = (
            authorization.get("wallet_address")
            or body.get("wallet_address")
            or (body.get("credit", {}).get("wallet_address") if isinstance(body.get("credit"), dict) else "")
            or ""
        )
        try:
            wallet_address = normalize_address(wallet_value)
        except ValueError:
            return {
                "ok": True,
                "valid": False,
                "ready": False,
                "hub_reachable": True,
                "reason_code": "bad_wallet_address",
                "user_message": "Paid overflow needs a valid connected wallet address before Hub key validation can pass.",
            }

        key_id = str(
            authorization.get("multisession_key_id")
            or authorization.get("key_id")
            or body.get("multisession_key_id")
            or body.get("key_id")
            or ""
        ).strip()
        if not key_id:
            account_id = wallet_account_id(wallet_address)
            account = self.server.credit_ledger.get_account(account_id)
            return {
                "ok": True,
                "valid": False,
                "ready": False,
                "hub_reachable": True,
                "reason_code": "missing_multisession_key_id",
                "user_message": "Paid overflow needs an active multi-session key before this Hub can spend bridged credits.",
                "wallet_address": wallet_address,
                "account_id": account_id,
                "account": account.as_dict(),
            }

        requested_chain_id = normalize_chain_id(
            authorization.get("chain_id")
            or body.get("chain_id")
            or ""
        )

        with self.server.multisession_key_store_lock:
            data = self._load_multisession_key_store_unlocked()
            record = data.get("keys", {}).get(key_id)
            if not isinstance(record, dict):
                record = None
            else:
                record = dict(record)

        account_id = wallet_account_id(wallet_address)
        account = self.server.credit_ledger.get_account(account_id)
        account_payload = account.as_dict()

        if not record or record.get("status") != "active":
            return {
                "ok": True,
                "valid": False,
                "ready": False,
                "hub_reachable": True,
                "reason_code": "key_not_active",
                "user_message": "The selected multi-session key is not active on this Hub.",
                "wallet_address": wallet_address,
                "account_id": account_id,
                "account": account_payload,
                "multisession_key_id": key_id,
            }

        try:
            record_wallet = normalize_address(record.get("wallet_address"))
        except ValueError:
            record_wallet = ""
        if record_wallet != wallet_address:
            return {
                "ok": True,
                "valid": False,
                "ready": False,
                "hub_reachable": True,
                "reason_code": "key_wallet_mismatch",
                "user_message": "The selected multi-session key belongs to a different wallet.",
                "wallet_address": wallet_address,
                "account_id": account_id,
                "account": account_payload,
                "multisession_key_id": key_id,
            }

        record_chain_id = normalize_chain_id(record.get("chain_id"))
        if requested_chain_id and record_chain_id and requested_chain_id != record_chain_id:
            return {
                "ok": True,
                "valid": False,
                "ready": False,
                "hub_reachable": True,
                "reason_code": "chain_id_mismatch",
                "user_message": "The selected multi-session key was issued for a different chain.",
                "wallet_address": wallet_address,
                "account_id": account_id,
                "account": account_payload,
                "multisession_key_id": key_id,
                "chain_id": requested_chain_id,
                "record_chain_id": record_chain_id,
            }
        if (requested_chain_id or record_chain_id) and (requested_chain_id or record_chain_id) != HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID:
            return {
                "ok": True,
                "valid": False,
                "ready": False,
                "hub_reachable": True,
                "reason_code": "unsupported_chain_id",
                "user_message": "Paid overflow is only enabled for the local dev chain.",
                "wallet_address": wallet_address,
                "account_id": account_id,
                "account": account_payload,
                "multisession_key_id": key_id,
                "chain_id": requested_chain_id or record_chain_id,
            }

        required_credit_wei = _hub_as_int(
            authorization.get("required_credit_wei", authorization.get("max_authorized_credit_wei", body.get("required_credit_wei", 0))),
            0,
            minimum=0,
        )
        if required_credit_wei <= 0:
            return {
                "ok": True,
                "valid": False,
                "ready": False,
                "hub_reachable": True,
                "reason_code": "missing_required_credit_wei",
                "user_message": "Paid overflow readiness requires an exact credit amount before this Hub can authorize spending.",
                "wallet_address": wallet_address,
                "account_id": account_id,
                "account": account_payload,
                "multisession_key_id": key_id,
            }
        spendable_credit_wei = account.available_credit_wei + account.held_credit_wei
        credit_ready = True
        reason_code = "active"
        user_message = "The selected multi-session key is active on this Hub."
        if required_credit_wei and spendable_credit_wei < required_credit_wei:
            credit_ready = False
            reason_code = "insufficient_spendable_credits"
            user_message = (
                f"The Hub sees insufficient spendable credits for this approximate authorization: "
                f"{credit_wei_to_display_text(spendable_credit_wei)} spendable, "
                f"{credit_wei_to_display_text(required_credit_wei)} required."
            )

        key_payload = {
            "id": str(record.get("id") or key_id),
            "status": str(record.get("status") or ""),
            "wallet_address": record_wallet,
            "chain_id": record_chain_id,
            "created_at": str(record.get("created_at") or ""),
            "revoked_at": str(record.get("revoked_at") or ""),
            "request_id": str(record.get("request_id") or ""),
            "origin": str(record.get("origin") or ""),
        }
        return {
            "ok": True,
            "valid": True,
            "ready": bool(credit_ready),
            "hub_reachable": True,
            "reason_code": reason_code,
            "user_message": user_message,
            "wallet_address": wallet_address,
            "account_id": account_id,
            "account": account_payload,
            "multisession_key_id": key_id,
            "chain_id": requested_chain_id or record_chain_id,
            "required_credit_wei": str(required_credit_wei),
            "required_credits_display": credit_wei_to_decimal_text(required_credit_wei),
            "available_credit_wei": str(account.available_credit_wei),
            "available_credits_display": credit_wei_to_decimal_text(account.available_credit_wei),
            "spendable_credit_wei": str(spendable_credit_wei),
            "spendable_credits_display": credit_wei_to_decimal_text(spendable_credit_wei),
            "credit_ready": bool(credit_ready),
            "key": key_payload,
        }

    def _worker_settlement_precision_places(self, worker_node_id: str, explicit: Any = None) -> int:
        if explicit is not None and str(explicit).strip() != "":
            return normalize_worker_payout_precision_places(explicit)
        worker = self.server.registry.get_worker(worker_node_id) if worker_node_id else None
        if worker is not None:
            return normalize_worker_payout_precision_places(getattr(worker, "settlement_precision_places", None))
        return DEFAULT_WORKER_PAYOUT_PRECISION_PLACES

    def _exact_payouts_requested(self, query: dict[str, list[str]] | None = None, body: dict[str, Any] | None = None) -> bool:
        values: list[Any] = []
        if query:
            for key in ("audit", "exact", "debug", "include_exact"):
                values.extend(query.get(key, []))
        if body:
            for key in ("audit", "exact", "debug", "include_exact"):
                if key in body:
                    values.append(body.get(key))
        return any(str(value).strip().lower() in {"1", "true", "yes", "audit", "exact"} for value in values)

    def _public_worker_settlement_totals(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a worker settlement view safe for non-audit callers.

        Exact claim and dust fields can correlate high-precision request prices to
        worker payouts, so the default worker settlement surface publishes only
        bucketed payout units and counts.  Audit callers can request the raw
        ledger response explicitly with ?audit=1 / exact=true.
        """

        def sanitize_claim(claim: dict[str, Any]) -> dict[str, Any]:
            clean = {
                "claim_id": str(claim.get("claim_id", "")),
                "worker_node_id": str(claim.get("worker_node_id", "")),
                "status": str(claim.get("status", "")),
                "created_at": str(claim.get("created_at", "")),
                "earning_count": len(claim.get("earning_ids", [])) if isinstance(claim.get("earning_ids"), list) else 0,
            }
            return clean

        def sanitize_batch(batch: dict[str, Any]) -> dict[str, Any]:
            return {
                "batch_id": str(batch.get("batch_id", "")),
                "worker_node_id": str(batch.get("worker_node_id", "")),
                "status": str(batch.get("status", "")),
                "claim_count": len(batch.get("claim_ids", [])) if isinstance(batch.get("claim_ids"), list) else 0,
                "worker_count": int(batch.get("worker_count", 0) or 0),
                "total_credits_published": int(batch.get("total_credits_published", 0) or 0),
                "precision_places": int(batch.get("precision_places", payload.get("precision_places", DEFAULT_WORKER_PAYOUT_PRECISION_PLACES)) or 0),
                "rounding_bucket_credits": int(batch.get("rounding_bucket_credits", payload.get("rounding_bucket_credits", 0)) or 0),
                "settlement_reference": str(batch.get("settlement_reference", "")),
                "settlement_tx_hash": str(batch.get("settlement_tx_hash", "")),
                "payout_rail": str(batch.get("payout_rail", "")),
                "operator_id": str(batch.get("operator_id", "")),
                "settlement_proof_id": str(batch.get("settlement_proof_id", "")),
                "created_at": str(batch.get("created_at", "")),
                "settled_at": str(batch.get("settled_at", "")),
            }

        return {
            "ok": bool(payload.get("ok", True)),
            "worker_node_id": str(payload.get("worker_node_id", "")),
            "precision_places": int(payload.get("precision_places", DEFAULT_WORKER_PAYOUT_PRECISION_PLACES) or 0),
            "rounding_bucket_credits": int(payload.get("rounding_bucket_credits", 0) or 0),
            "settleable_units_published": int(payload.get("settleable_units_published", 0) or 0),
            "settleable_claim_count": int(payload.get("settleable_claim_count", 0) or 0),
            "open_batch_count": int(payload.get("open_batch_count", 0) or 0),
            "settled_batch_count": int(payload.get("settled_batch_count", 0) or 0),
            "settled_units_published": int(payload.get("settled_units_published", 0) or 0),
            "can_create_batch": bool(payload.get("can_create_batch", False)),
            "block_reason": str(payload.get("block_reason", "")),
            "claims": [sanitize_claim(claim) for claim in payload.get("claims", []) if isinstance(claim, dict)],
            "batches": [sanitize_batch(batch) for batch in payload.get("batches", []) if isinstance(batch, dict)],
            "privacy": {
                "exact_amounts_hidden": True,
                "exact_amounts_visible": False,
                "exact_claim_amounts_hidden": True,
                "bridge_dust_hidden": True,
                "rounding": "floor_to_precision",
                "audit_hint": "append ?audit=1 or exact=1 to view internal reconciliation fields",
            },
        }


    def _remote_overflow_prompt_preview(self, messages_payload: Any, *, limit: int = 280) -> str:
        chunks: list[str] = []
        if isinstance(messages_payload, list):
            for item in messages_payload:
                if isinstance(item, dict):
                    content = str(item.get("content") or "")
                    if content:
                        chunks.append(content)
        preview = " ".join(" ".join(chunks).replace("\r\n", "\n").replace("\r", "\n").split())
        if not preview:
            preview = "the submitted chat request"
        return preview[:limit]

    def _remote_overflow_token_estimate(self, messages_payload: list[Any]) -> tuple[int, int, int]:
        content_chars = 0
        attachment_count = 0
        for item in messages_payload:
            if not isinstance(item, dict):
                continue
            content_chars += len(str(item.get("content") or ""))
            attachments = item.get("attachments")
            if isinstance(attachments, list):
                attachment_count += len(attachments)
        estimated_input_tokens = max(1, int((content_chars + 3) // 4) + attachment_count * 256)
        return estimated_input_tokens, content_chars, attachment_count

    def _paid_overflow_authorization_from_metadata(
        self,
        *,
        body: dict[str, Any],
        metadata: dict[str, Any],
        messages_payload: list[Any],
    ) -> dict[str, Any] | None:
        authorization = metadata.get("payment_authorization")
        if not isinstance(authorization, dict):
            authorization = body.get("payment_authorization") if isinstance(body.get("payment_authorization"), dict) else {}
        if not authorization:
            return None

        if not _hub_as_bool(authorization.get("paid_overflow_enabled", metadata.get("paid_overflow_enabled")), False):
            raise HubCreditAuthorizationError("Paid overflow is disabled for this request.")

        kind = str(authorization.get("kind") or "").strip().lower()
        if kind != "multisession_key":
            raise HubCreditAuthorizationError("Paid overflow requires an active multi-session key authorization.")

        wallet_address = normalize_address(authorization.get("wallet_address") or metadata.get("wallet_address") or body.get("wallet_address"))
        key_id = str(
            authorization.get("multisession_key_id")
            or authorization.get("key_id")
            or metadata.get("multisession_key_id")
            or ""
        ).strip()
        if not key_id:
            raise HubCreditAuthorizationError("Paid overflow requires a multi-session key id.")

        with self.server.multisession_key_store_lock:
            data = self._load_multisession_key_store_unlocked()
            record = data.get("keys", {}).get(key_id)
            if not isinstance(record, dict) or record.get("status") != "active":
                raise HubCreditAuthorizationError("The multi-session key is not active.")
            record_wallet = normalize_address(record.get("wallet_address"))
            if record_wallet != wallet_address:
                raise HubCreditAuthorizationError("The multi-session key does not belong to the requested wallet.")
            record_chain_id = normalize_chain_id(record.get("chain_id"))

        requested_chain_id = normalize_chain_id(authorization.get("chain_id") or metadata.get("chain_id") or record_chain_id)
        if requested_chain_id and record_chain_id and requested_chain_id != record_chain_id:
            raise HubCreditAuthorizationError("The multi-session key chain id does not match this request.")
        if requested_chain_id and requested_chain_id != HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID:
            raise HubCreditAuthorizationError("Paid overflow is only enabled for the local dev chain.")

        estimated_input_tokens, content_chars, attachment_count = self._remote_overflow_token_estimate(messages_payload)
        max_output_tokens = _hub_as_int(
            authorization.get("max_output_tokens", metadata.get("max_output_tokens", body.get("max_output_tokens"))),
            1024,
            minimum=1,
            maximum=128_000,
        )
        credits_per_token_text = str(
            authorization.get("credits_per_token", metadata.get("credits_per_token", body.get("credits_per_token", "0.001")))
        ).strip() or "0.001"
        credits_per_token_wei = _hub_credit_wei_from_decimal_text(
            credits_per_token_text,
            "0.001",
            minimum_wei=1_000_000_000_000,
            maximum_wei=1_000_000 * CREDIT_WEI_PER_CREDIT,
        )
        estimated_token_count = estimated_input_tokens + max_output_tokens
        estimated_credit_wei = credit_wei_product(estimated_token_count, credits_per_token_wei)
        if estimated_credit_wei <= 0:
            estimated_credit_wei = 1
        max_authorized_credit_wei = _hub_as_int(
            authorization.get("max_authorized_credit_wei"),
            0,
            minimum=0,
        )
        if max_authorized_credit_wei <= 0:
            raise HubCreditAuthorizationError("Paid overflow authorization requires an exact maximum credit amount.")
        if max_authorized_credit_wei < estimated_credit_wei:
            raise HubCreditAuthorizationError(
                "Paid overflow authorization is below the Hub estimate: "
                f"{credit_wei_to_display_text(max_authorized_credit_wei)} authorized, "
                f"{credit_wei_to_display_text(estimated_credit_wei)} required."
            )

        account_id = wallet_account_id(wallet_address)
        account = self.server.credit_ledger.get_account(account_id)
        spendable_credit_wei = account.available_credit_wei + account.held_credit_wei
        if spendable_credit_wei < estimated_credit_wei:
            raise HubPaymentRequired(
                f"Insufficient Compute Credits for account {account_id}: "
                f"{credit_wei_to_display_text(spendable_credit_wei)} spendable, "
                f"{credit_wei_to_display_text(estimated_credit_wei)} required."
            )
        return {
            "kind": "multisession_key",
            "wallet_address": wallet_address,
            "account_id": account_id,
            "multisession_key_id": key_id,
            "chain_id": requested_chain_id or record_chain_id,
            "estimated_input_tokens": estimated_input_tokens,
            "max_output_tokens": max_output_tokens,
            "credits_per_token": credit_wei_to_decimal_text(credits_per_token_wei),
            "credits_per_token_wei": str(credits_per_token_wei),
            "estimated_max_credits_approx": credit_wei_to_decimal_text(estimated_credit_wei),
            "estimated_max_credit_wei": str(estimated_credit_wei),
            "required_credit_wei": str(estimated_credit_wei),
            "required_credits_display": credit_wei_to_decimal_text(estimated_credit_wei),
            "max_authorized_credit_wei": str(max_authorized_credit_wei),
            "content_chars": content_chars,
            "attachment_count": attachment_count,
            "approximation_only": True,
        }

    def _log_remote_overflow_event(
        self,
        event: str,
        *,
        remote_overflow_request_id: str = "",
        hub_request_id: str = "",
        message: str = "",
    ) -> None:
        parts = [f"[remote-hub-overflow] {event}"]
        if remote_overflow_request_id:
            parts.append(f"remote_overflow_request_id={remote_overflow_request_id}")
        if hub_request_id:
            parts.append(f"hub_request_id={hub_request_id}")
        if message:
            parts.append(message)
        print(" ".join(parts), flush=True)

    def _handle_remote_overflow_safe_chat(self, body: dict[str, Any]) -> dict[str, Any]:
        messages_payload = body.get("messages")
        if not isinstance(messages_payload, list):
            messages_payload = []
        model = str(body.get("model") or self.server.config.model or "remote-hub-ai")
        client_node_id = str(body.get("client_node_id") or self.server.config.hub_client_node_id)
        remote_overflow_request_id = str(
            body.get("remote_overflow_request_id")
            or body.get("correlation_id")
            or body.get("request_id")
            or ""
        ).strip()
        if not remote_overflow_request_id:
            remote_overflow_request_id = "remote-overflow-" + hashlib.sha256(
                json.dumps(
                    {
                        "model": model,
                        "client_node_id": client_node_id,
                        "messages": messages_payload,
                        "stamp": time.time_ns(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()[:16]

        incoming_metadata = dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {}
        payment_authorization = self._paid_overflow_authorization_from_metadata(
            body=body,
            metadata=incoming_metadata,
            messages_payload=messages_payload,
        )
        request_seed = {
            "remote_overflow_request_id": remote_overflow_request_id,
            "model": model,
            "client_node_id": client_node_id,
        }
        if payment_authorization:
            request_seed["account_id"] = payment_authorization["account_id"]
        else:
            request_seed["stamp"] = time.time_ns()
        hub_request_id = "hub_overflow_" + hashlib.sha256(
            json.dumps(request_seed, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:20]

        self._log_remote_overflow_event(
            "request received",
            remote_overflow_request_id=remote_overflow_request_id,
            hub_request_id=hub_request_id,
        )

        payment_receipt: dict[str, Any] | None = None
        try:
            prompt_preview = self._remote_overflow_prompt_preview(messages_payload)
            if payment_authorization:
                content = (
                    "Remote Hub AI response received.\n"
                    f"Paid overflow charged {payment_authorization['required_credits_display']} credit"
                    f"{'' if payment_authorization['required_credits_display'] == '1' else 's'} from the bridged wallet account.\n\n"
                    f"Prompt preview: {prompt_preview}"
                )
            else:
                content = (
                    "Remote Hub AI response received.\n"
                    "No credits were spent, and no real paid worker was contacted.\n\n"
                    f"Prompt preview: {prompt_preview}"
                )

            if payment_authorization:
                spend_result = self.server.credit_ledger.spend_request_credit_wei(
                    account_id=payment_authorization["account_id"],
                    request_id=hub_request_id,
                    credit_wei=payment_authorization["required_credit_wei"],
                    memo=f"paid remote overflow charge for {remote_overflow_request_id}",
                    metadata={
                        "remote_overflow_request_id": remote_overflow_request_id,
                        "wallet_address": payment_authorization["wallet_address"],
                        "multisession_key_id": payment_authorization["multisession_key_id"],
                        "max_output_tokens": payment_authorization["max_output_tokens"],
                        "credits_per_token": payment_authorization["credits_per_token"],
                        "credits_per_token_wei": payment_authorization["credits_per_token_wei"],
                        "estimated_input_tokens": payment_authorization["estimated_input_tokens"],
                        "estimated_max_credits_approx": payment_authorization["estimated_max_credits_approx"],
                        "required_credit_wei": payment_authorization["required_credit_wei"],
                        "approximation_only": True,
                        "direct_spend": True,
                    },
                )
                charge_payload = spend_result.get("charge") if isinstance(spend_result.get("charge"), dict) else {}
                account_payload = spend_result.get("account") if isinstance(spend_result.get("account"), dict) else {}
                payment_receipt = {
                    "account_id": payment_authorization["account_id"],
                    "wallet_address": payment_authorization["wallet_address"],
                    "charged_credits_display": payment_authorization["required_credits_display"],
                    "charged_credit_wei": payment_authorization["required_credit_wei"],
                    "charge_id": str(charge_payload.get("charge_id") or ""),
                    "request_id": hub_request_id,
                    "remote_overflow_request_id": remote_overflow_request_id,
                    "available_credits_after": account_payload.get("available_credits_display", account_payload.get("available_credits", 0)),
                    "available_credit_wei_after": account_payload.get("available_credit_wei", "0"),
                    "held_credits_after": account_payload.get("held_credits_display", account_payload.get("held_credits", 0)),
                    "held_credit_wei_after": account_payload.get("held_credit_wei", "0"),
                    "spent_credits_after": account_payload.get("spent_credits_display", account_payload.get("spent_credits", 0)),
                    "spent_credit_wei_after": account_payload.get("spent_credit_wei", "0"),
                    "legacy_holds_cancelled": list(spend_result.get("legacy_holds_cancelled") or []),
                    "direct_spend": True,
                    "max_output_tokens": payment_authorization["max_output_tokens"],
                    "credits_per_token": payment_authorization["credits_per_token"],
                    "credits_per_token_wei": payment_authorization["credits_per_token_wei"],
                    "estimated_input_tokens": payment_authorization["estimated_input_tokens"],
                    "estimated_max_credits_approx": payment_authorization["estimated_max_credits_approx"],
                    "estimated_max_credit_wei": payment_authorization["estimated_max_credit_wei"],
                    "approximation_only": True,
                    "idempotent": bool(spend_result.get("idempotent")),
                    "authorization": {
                        "kind": payment_authorization["kind"],
                        "multisession_key_id": payment_authorization["multisession_key_id"],
                        "chain_id": payment_authorization["chain_id"],
                    },
                }
                self._log_remote_overflow_event(
                    "credit spend recorded",
                    remote_overflow_request_id=remote_overflow_request_id,
                    hub_request_id=hub_request_id,
                    message=(
                        f"account_id={payment_authorization['account_id']} "
                        f"charged_credit_wei={payment_authorization['required_credit_wei']} "
                        f"charged_credits_display={payment_authorization['required_credits_display']} "
                        f"charge_id={payment_receipt['charge_id']}"
                    ),
                )

        except Exception:
            raise

        metadata = {
            **incoming_metadata,
            "remote_overflow": True,
            "remote_hub_ai": True,
            "remote_hub_surface": "remote-overflow-safe-chat",
            "remote_hub_observable_passthrough": True,
            "safe_remote_hub_path": True,
            "remote_overflow_request_id": remote_overflow_request_id,
            "credit_spent": bool(payment_receipt),
            "no_credit_spent": not bool(payment_receipt),
            "no_real_paid_worker_contacted": True,
            "no_real_remote_worker_contacted": True,
            "hub": {
                "request_id": hub_request_id,
                "remote_overflow_request_id": remote_overflow_request_id,
                "client_node_id": client_node_id,
                "model": model,
                "surface": "/api/hub/remote-overflow/safe-chat",
                "security_mode": "remote-overflow-paid" if payment_receipt else "remote-overflow-safe-preview",
                "credit_spent": bool(payment_receipt),
                "no_credit_spent": not bool(payment_receipt),
                "no_real_paid_worker_contacted": True,
            },
        }
        if payment_receipt:
            metadata["payment"] = payment_receipt

        self._log_remote_overflow_event(
            "response returned to chat console",
            remote_overflow_request_id=remote_overflow_request_id,
            hub_request_id=hub_request_id,
        )
        return {
            "ok": True,
            "content": content,
            "provider": "remote-hub-ai",
            "model": model,
            "metadata": metadata,
        }


    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path in HUB_ADMIN_ROUTES:
            html = render_hub_admin_html()
            self._send_bytes(html.encode("utf-8"), content_type="text/html; charset=utf-8")
            return
        if path == "/api/hub/v1/admin/bootstrap":
            self._send_json(
                build_admin_bootstrap_payload(
                    config=self.server.config,
                    registry=self.server.registry,
                    dispatcher=self.server.dispatcher,
                    energy_ledger=self.server.energy_ledger,
                    credit_ledger=self.server.credit_ledger,
                    credit_indexer=self.server.credit_indexer,
                    serving_hub=serving_hub_identity_for_server(self.server),
                )
            )
            return
        if path == "/api/hub/v1/health":
            self._send_json(
                {
                    "ok": True,
                    "service": "main-computer-hub",
                    "api_version": "v1",
                    "security_profile": HUB_SECURITY_PROFILE,
                    "network_key": getattr(self.server.config, "hub_network", ""),
                    "cell_id": os.environ.get("MC_ALLFATHER_CELL_ID", ""),
                    "bootstrap_hub": False,
                    "full_main_computer_hub": True,
                    "hub_remote_manifest": load_allfather_hub_remote_manifest(),
                }
            )
            return
        if path in {"/api/hub/status", "/api/hub/v1/status"}:
            status = self.server.registry.status()
            status["api_version"] = "v1" if path.startswith("/api/hub/v1/") else "legacy"
            status["serving_hub"] = serving_hub_identity_for_server(self.server)
            status["network"] = {
                "network_key": self.server.config.hub_network,
                "display_name": self.server.config.hub_network_display_name,
                "kind": self.server.config.hub_network_kind,
                "chain_id": self.server.config.chain_id,
                "chain_id_hex": hex(self.server.config.chain_id) if self.server.config.chain_id is not None else None,
                "chain_rpc_url": self.server.config.chain_rpc_url,
                "chain_rpc_url_source": self.server.config.chain_rpc_url_source,
                "chain_id_source": self.server.config.chain_id_source,
                "hub_url": self.server.config.hub_url,
                "hub_public_url": self.server.config.hub_url,
                "hub_bind_host": self.server.config.hub_bind_host,
                "hub_bind_port": self.server.config.hub_bind_port,
                "hub_host": self.server.config.hub_bind_host,
                "hub_port": self.server.config.hub_bind_port,
                "hub_runtime_dir": str(self.server.hub_root),
                "network_config_path": str(self.server.config.hub_network_config_path)
                if self.server.config.hub_network_config_path
                else None,
            }
            bridge_backend_status = getattr(self.server.bridge_backend, "status", lambda: {"backend": self.server.config.hub_bridge_backend})()
            status["bridge_backend"] = bridge_backend_status
            status.update(self.server.ring_admission_config.public_status())
            status["ring_admission_rejection_audit_count"] = self.server.registry.ring_admission_audit_count()
            ring_audit_diag = self.server.ring_admission_audit_diagnostics()
            status["ring_admission_rejection_audit_write_failure_count"] = ring_audit_diag["write_failure_count"]
            status["ring_admission_rejection_audit_write_failure_suppressed_since_last_log"] = ring_audit_diag[
                "suppressed_write_failure_count"
            ]
            status["ring_admission_rejection_audit_last_write_failure"] = ring_audit_diag["last_write_failure"]
            status["hub_remote_manifest"] = load_allfather_hub_remote_manifest()
            status["security"] = {
                "high_security_default": self.server.config.hub_high_security,
                "hub_blind_envelopes": self.server.config.hub_high_security,
                "encryption_profile": HUB_SECURITY_PROFILE,
                "transport": "https-required-except-loopback",
                "allow_insecure_dev_network": self.server.config.hub_allow_insecure_dev_network,
            }
            exact_payouts = self._exact_payouts_requested(query)
            status["energy"] = self.server.energy_ledger.status(exact=exact_payouts)
            self._send_json(status)
            return
        if path == "/api/hub/v1/metrics":
            self._send_json(self.server.dispatcher.metrics())
            return
        if path == "/api/hub/v1/ring-control/feedback-summary":
            try:
                limit = int(query.get("limit", ["500"])[0] or 500)
                self._send_json(self.server.dispatcher.ring_control_feedback_summary(limit=limit))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/hub/v1/workers":
            include_endpoint = str(query.get("debug", [""])[0]).lower() in {"1", "true", "yes"}
            status = self.server.registry.status()
            workers = [
                HubWorkerSummary.from_worker_payload(worker, include_endpoint=include_endpoint).as_dict()
                for worker in status.get("workers", [])
                if isinstance(worker, dict)
            ]
            self._send_json(
                {
                    "ok": True,
                    "workers": workers,
                    "worker_count": len(workers),
                    "available_worker_count": status.get("available_worker_count", 0),
                    "stale_worker_count": status.get("stale_worker_count", 0),
                }
            )
            return
        if path in {"/api/hub/v1/workers/claims", "/api/hub/v1/credits/worker-claims"}:
            worker_node_id = query.get("worker_node_id", [""])[0] or query.get("node_id", [""])[0]
            try:
                self._send_json(self.server.credit_ledger.worker_claim_totals(worker_node_id))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if path in {"/api/hub/v1/workers/settlements", "/api/hub/v1/credits/worker-settlements"}:
            worker_node_id = query.get("worker_node_id", [""])[0] or query.get("node_id", [""])[0]
            try:
                precision = self._worker_settlement_precision_places(worker_node_id, query.get("precision_places", [None])[0])
                totals = self.server.credit_ledger.worker_settlement_totals(worker_node_id, precision_places=precision)
                if self._exact_payouts_requested(query):
                    self._send_json(totals)
                else:
                    self._send_json(self._public_worker_settlement_totals(totals))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if path.startswith("/api/hub/v1/workers/") and path.endswith("/feedback-summary"):
            worker_id = path.removeprefix("/api/hub/v1/workers/").removesuffix("/feedback-summary").strip("/")
            if not worker_id or "/" in worker_id:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                limit = int(query.get("limit", ["500"])[0] or 500)
                self._send_json(
                    self.server.dispatcher.worker_reliability_summary(
                        worker_node_id=worker_id,
                        include_private=True,
                        limit=limit,
                    )
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if path.startswith("/api/hub/v1/workers/"):
            worker_id = path.removeprefix("/api/hub/v1/workers/").strip("/")
            if not worker_id or "/" in worker_id:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            include_endpoint = str(query.get("debug", [""])[0]).lower() in {"1", "true", "yes"}
            worker = self.server.registry.get_worker(worker_id)
            if worker is None:
                self._send_json({"error": f"Unknown hub worker: {worker_id}"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({"ok": True, "worker": HubWorkerSummary.from_worker_payload(worker.as_dict(), include_endpoint=include_endpoint).as_dict()})
            return
        if path == "/api/hub/v1/models":
            status = self.server.registry.status()
            models = {
                str(model).strip()
                for worker in status.get("workers", [])
                if isinstance(worker, dict)
                for model in (worker.get("models") if isinstance(worker.get("models"), list) else [worker.get("model", "")])
                if str(model).strip()
            }
            if self.server.config.model:
                models.add(str(self.server.config.model))
            self._send_json({"ok": True, "models": sorted(models)})
            return
        if path == "/api/hub/v1/requests":
            states_param = str(query.get("state", [""])[0]).strip()
            states = {item.strip() for item in states_param.split(",") if item.strip()} if states_param else None
            limit = int(query.get("limit", ["100"])[0] or 100)
            requests = self.server.dispatcher.list_requests(limit=limit, states=states)
            self._send_json({"ok": True, "requests": requests, "request_count": len(requests)})
            return
        if path.startswith("/api/hub/v1/requests/") and path.endswith("/feedback"):
            request_id = path.removeprefix("/api/hub/v1/requests/").removesuffix("/feedback").strip("/")
            if not request_id or "/" in request_id:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                self._send_json(
                    self.server.dispatcher.get_request_feedback(
                        request_id,
                        account_id=str(query.get("account_id", [""])[0] or ""),
                    )
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
            return
        if path.startswith("/api/hub/v1/requests/") and (path.endswith("/result") or path.endswith("/pickup")):
            suffix = "/result" if path.endswith("/result") else "/pickup"
            request_id = path.removeprefix("/api/hub/v1/requests/").removesuffix(suffix).strip("/")
            if not request_id or "/" in request_id:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                payload = self.server.dispatcher.pickup_request_result(
                    request_id,
                    account_id=str(query.get("account_id", [""])[0] or ""),
                    client_node_id=str(query.get("client_node_id", [""])[0] or ""),
                )
                self._send_json(payload)
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
            return
        if path.startswith("/api/hub/v1/requests/") and path.endswith("/charges"):
            request_id = path.removeprefix("/api/hub/v1/requests/").removesuffix("/charges").strip("/")
            if not request_id or "/" in request_id:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            charges = [
                charge.as_dict()
                for charge in self.server.credit_ledger.list_charges(request_id=request_id)
            ]
            self._send_json({"ok": True, "request_id": request_id, "charges": charges, "charge_count": len(charges)})
            return
        if path.startswith("/api/hub/v1/requests/") and path.endswith("/stream"):
            request_id = path.removeprefix("/api/hub/v1/requests/").removesuffix("/stream").strip("/")
            if not request_id or "/" in request_id:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                # Verify the request exists before switching the response into JSONL streaming mode.
                self.server.dispatcher.get_request_status(request_id)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                return
            self._stream_request_events(request_id, query)
            return
        if path.startswith("/api/hub/v1/requests/") and path.endswith("/events"):
            request_id = path.removeprefix("/api/hub/v1/requests/").removesuffix("/events").strip("/")
            if not request_id or "/" in request_id:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                self._send_json({"ok": True, "request_id": request_id, "events": self.server.dispatcher.get_request_events(request_id)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
            return
        if path.startswith("/api/hub/v1/requests/"):
            request_id = path.removeprefix("/api/hub/v1/requests/").strip("/")
            if not request_id or "/" in request_id:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                self._send_json({"ok": True, "request": self.server.dispatcher.get_request_status(request_id)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
            return
        if path == "/api/hub/v1/credits":
            self._send_json(self.server.credit_ledger.status())
            return
        if path == "/api/hub/v1/bridge/mock-chain/wallets":
            if not hasattr(self.server.credit_ledger, "mock_chain_wallet_status"):
                self._send_json({"ok": False, "error": "mock chain bridge is not available on this ledger."}, status=HTTPStatus.NOT_IMPLEMENTED)
                return
            wallet_address = query.get("wallet_address", [""])[0]
            self._send_json(self.server.credit_ledger.mock_chain_wallet_status(wallet_address))
            return
        if path == "/api/hub/v1/bridge/wallet-locks":
            if not hasattr(self.server.credit_ledger, "wallet_lock_status"):
                self._send_json({"ok": False, "error": "wallet locks are not available on this ledger."}, status=HTTPStatus.NOT_IMPLEMENTED)
                return
            wallet_address = query.get("wallet_address", [""])[0]
            self._send_json(self.server.credit_ledger.wallet_lock_status(wallet_address))
            return
        if path == "/api/hub/v1/bridge/audit":
            if not hasattr(self.server.credit_ledger, "list_bridge_audit"):
                self._send_json({"ok": False, "error": "bridge audit is not available on this ledger."}, status=HTTPStatus.NOT_IMPLEMENTED)
                return
            limit = int(query.get("limit", ["100"])[0] or 100)
            events = self.server.credit_ledger.list_bridge_audit(
                wallet_address=query.get("wallet_address", [""])[0],
                account_id=query.get("account_id", [""])[0],
                worker_node_id=query.get("worker_node_id", [""])[0],
                limit=limit,
            )
            self._send_json({"ok": True, "events": events, "event_count": len(events)})
            return
        if path == "/api/hub/v1/credits/indexer":
            self._send_json(self.server.credit_indexer.status())
            return
        if path == "/api/hub/v1/credits/wallet-funding/completion":
            self._send_json(self.server.credit_bridge_completion.status())
            return
        if path == "/api/hub/v1/credits/accounts":
            limit = int(query.get("limit", ["100"])[0] or 100)
            accounts = [account.as_dict() for account in self.server.credit_ledger.list_accounts(limit=limit)]
            self._send_json({"ok": True, "accounts": accounts, "account_count": len(accounts)})
            return
        if path == "/api/hub/v1/credits/balance":
            wallet_address = str(query.get("wallet_address", [""])[0]).strip()
            if wallet_address:
                try:
                    self._send_json(self._wallet_credit_balance_payload(wallet_address))
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            account_id = query.get("account_id", [self.server.config.hub_client_node_id])[0]
            account = self.server.credit_ledger.get_account(account_id)
            self._send_json({"ok": True, "account": account.as_dict(), "unit": self.server.credit_ledger.status()["unit"]})
            return
        if path.startswith("/api/hub/v1/credits/wallets/") and path.endswith("/balance"):
            wallet_address = path.removeprefix("/api/hub/v1/credits/wallets/").removesuffix("/balance").strip("/")
            if not wallet_address or "/" in wallet_address:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                self._send_json(self._wallet_credit_balance_payload(wallet_address))
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/hub/v1/credits/transactions":
            account_id = query.get("account_id", [""])[0]
            limit = int(query.get("limit", ["100"])[0] or 100)
            transactions = [
                tx.as_dict()
                for tx in self.server.credit_ledger.list_transactions(account_id=account_id, limit=limit)
            ]
            self._send_json({"ok": True, "transactions": transactions, "transaction_count": len(transactions)})
            return
        if path == "/api/hub/v1/credits/charges":
            account_id = query.get("account_id", [""])[0]
            request_id = query.get("request_id", [""])[0]
            limit = int(query.get("limit", ["100"])[0] or 100)
            charges = [
                charge.as_dict()
                for charge in self.server.credit_ledger.list_charges(
                    account_id=account_id,
                    request_id=request_id,
                    limit=limit,
                )
            ]
            self._send_json({"ok": True, "charges": charges, "charge_count": len(charges)})
            return
        if path == "/api/hub/v1/credits/bridge-reconciliation":
            account_id = query.get("account_id", [""])[0]
            self._send_json(self.server.credit_ledger.bridge_reconciliation_totals(account_id))
            return
        if path in {"/api/hub/v1/credits/deposits", "/api/hub/v1/credits/purchases"}:
            account_id = query.get("account_id", [""])[0]
            limit = int(query.get("limit", ["100"])[0] or 100)
            deposits = [
                deposit.as_dict()
                for deposit in self.server.credit_ledger.list_deposits(account_id=account_id, limit=limit)
            ]
            payload = {
                "ok": True,
                "deposits": deposits,
                "deposit_count": len(deposits),
                # Legacy aliases retained while older scripts/tests migrate.
                "purchases": deposits,
                "purchase_count": len(deposits),
            }
            self._send_json(payload)
            return
        if path == "/api/hub/v1/credits/holds":
            # Holds are no longer part of the credit golden path.  Keep this
            # read endpoint harmless for old diagnostics, but never report an
            # active hold that could block spendable balance.
            self._send_json({"ok": True, "holds_disabled": True, "holds": [], "hold_count": 0})
            return
        if path == "/api/hub/v1/credits/worker-earnings":
            worker_node_id = query.get("worker_node_id", [""])[0]
            request_id = query.get("request_id", [""])[0]
            limit = int(query.get("limit", ["100"])[0] or 100)
            earnings = [
                earning.as_private_dict()
                for earning in self.server.credit_ledger.list_worker_earnings(
                    worker_node_id=worker_node_id,
                    request_id=request_id,
                    limit=limit,
                )
            ]
            self._send_json({"ok": True, "worker_earnings": earnings, "worker_earning_count": len(earnings)})
            return
        if path in {"/api/hub/payouts", "/api/hub/v1/payouts"}:
            node_id = query.get("node_id", [""])[0]
            try:
                exact_payouts = self._exact_payouts_requested(query)
                precision = self._worker_settlement_precision_places(node_id, query.get("precision_places", [None])[0])
                self._send_json(self.server.energy_ledger.payout_summary(node_id, exact=exact_payouts, precision_places=precision))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/hub/remote-overflow/safe-chat":
                try:
                    self._send_json(self._handle_remote_overflow_safe_chat(self._read_json()))
                except HubPaymentRequired as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.PAYMENT_REQUIRED)
                except HubCreditAuthorizationError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                return
            if path == "/api/hub/v1/credits/multisession-keys/request":
                try:
                    self._send_json(self._handle_multisession_key_request(self._read_json()))
                except ValueError as exc:
                    status = HTTPStatus.CONFLICT if "not spendable" in str(exc) else HTTPStatus.BAD_REQUEST
                    self._send_json({"ok": False, "error": str(exc)}, status=status)
                return
            if path == "/api/hub/v1/credits/multisession-keys/revoke":
                try:
                    self._send_json(self._handle_multisession_key_revoke(self._read_json()))
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if path == "/api/hub/v1/credits/multisession-keys/validate":
                try:
                    self._send_json(self._handle_multisession_key_validate(self._read_json()))
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if path in {"/api/hub/workers/connect", "/api/hub/v1/workers/connect"}:
                try:
                    result = self._handle_worker_start_registration(self._read_json())
                    status = HTTPStatus.FORBIDDEN if result.get("error") == "ring_not_allowed" else HTTPStatus.OK
                    self._send_json(result, status=status)
                except HubCreditAuthorizationError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if path in {"/api/hub/workers/register", "/api/hub/v1/workers/register"}:
                diag_id, diag_started_at = self._worker_route_diag_start("worker.register", path)
                if not self._worker_route_enter_or_reject(diag_id, "worker.register", diag_started_at):
                    return
                try:
                    self._worker_route_diag_step(diag_id, "worker.register", "read_json.start", diag_started_at)
                    body = self._read_json()
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.register",
                        "read_json.done",
                        diag_started_at,
                        node_id=str(body.get("node_id", "")),
                        body_keys=sorted(str(key) for key in body.keys()),
                    )
                    capabilities = dict(body.get("capabilities", {})) if isinstance(body.get("capabilities"), dict) else {}
                    pricing = dict(body.get("pricing", {})) if isinstance(body.get("pricing"), dict) else {}
                    execution = dict(body.get("execution", {})) if isinstance(body.get("execution"), dict) else {}
                    if pricing:
                        capabilities["pricing"] = pricing
                    if execution:
                        capabilities["execution"] = execution
                    if body.get("execution_mode") is not None:
                        capabilities["execution_mode"] = str(body.get("execution_mode") or "")
                    worker_authorization = self._authorize_worker_route(
                        body=body,
                        worker_id=str(body.get("node_id", "")),
                        registration=True,
                    )
                    if worker_authorization:
                        capabilities["wallet_address"] = worker_authorization["wallet_address"]
                        capabilities["credit_wallet"] = worker_authorization["wallet_address"]
                        capabilities["multisession_key_id"] = worker_authorization["multisession_key_id"]
                        capabilities["multisession_key_authorized"] = True
                        capabilities["auth_mode"] = "multisession-wallet"
                        if worker_authorization.get("chain_id"):
                            capabilities["chain_id"] = worker_authorization["chain_id"]
                    decision, wallet_address = self._evaluate_ring_registration(body=body, capabilities=capabilities)
                    if not decision.ok:
                        worker_node_id = str(body.get("node_id", ""))
                        worker_instance_id = str(body.get("worker_instance_id") or body.get("connection_id") or "")
                        self._record_ring_admission_rejection(
                            wallet_address=wallet_address,
                            node_id=worker_node_id,
                            worker_instance_id=worker_instance_id,
                            requested_ring=decision.requested_ring,
                            minimum_allowed_ring=decision.minimum_allowed_ring,
                            fallback_ring=decision.fallback_ring,
                            error=decision.error or "ring_not_allowed",
                            message=decision.message,
                        )
                        self._send_json(
                            self._ring_admission_rejection_payload(
                                decision=decision,
                                wallet_address=wallet_address,
                                node_id=worker_node_id,
                            ),
                            status=HTTPStatus.FORBIDDEN,
                        )
                        return
                    self._apply_accepted_ring_admission(
                        capabilities=capabilities,
                        decision=decision,
                        wallet_address=wallet_address,
                    )
                    raw_price = body.get("credits_per_request")
                    if raw_price is None and pricing:
                        raw_price = pricing.get("credits_per_request")
                    pricing_type = str(pricing.get("pricing_type") or pricing.get("type") or "").strip().lower()
                    if pricing_type in {"none", "unpriced", "unpriced_v0"}:
                        capabilities["phase9_unpriced"] = True
                        raw_price = self.server.config.hub_credits_per_request
                    self._worker_route_diag_step(diag_id, "worker.register", "registry.register_worker.start", diag_started_at)
                    worker = self.server.registry.register_worker(
                        node_id=str(body.get("node_id", "")),
                        endpoint=str(body.get("endpoint", "")),
                        model=str(body.get("model", "")),
                        models=[str(item) for item in body.get("models", [])] if isinstance(body.get("models"), list) else None,
                        capabilities=capabilities,
                        credits_per_request=raw_price if raw_price is not None else self.server.config.hub_credits_per_request,
                        settlement_precision_places=body.get("settlement_precision_places"),
                        queue_depth=int(body.get("queue_depth", 0) or 0),
                        active_requests=int(body.get("active_requests", 0) or 0),
                        max_concurrency=int(body.get("max_concurrency", 1) or 1),
                        worker_instance_id=str(body.get("worker_instance_id") or body.get("connection_id") or ""),
                    )
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.register",
                        "registry.register_worker.done",
                        diag_started_at,
                        worker_node_id=worker.node_id,
                    )
                    self._worker_route_diag_step(diag_id, "worker.register", "energy_ledger.register_node.start", diag_started_at, worker_node_id=worker.node_id)
                    self.server.energy_ledger.register_node(worker.node_id, "gpu-worker", worker.endpoint)
                    self._worker_route_diag_step(diag_id, "worker.register", "energy_ledger.register_node.done", diag_started_at, worker_node_id=worker.node_id)
                    self._worker_route_diag_step(diag_id, "worker.register", "hub_status.omitted", diag_started_at)
                    self._worker_route_diag_step(diag_id, "worker.register", "send_json.start", diag_started_at, status=200)
                    self._send_json(self._worker_route_success_payload(worker))
                    self._worker_route_diag_step(diag_id, "worker.register", "send_json.done", diag_started_at, status=200)
                    return
                except HubCreditAuthorizationError as exc:
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.register",
                        "auth_error",
                        diag_started_at,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                    return
                except Exception as exc:
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.register",
                        "exception",
                        diag_started_at,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    raise
                finally:
                    self._worker_route_exit(diag_id, "worker.register", diag_started_at)
            if path in {"/api/hub/v1/workers/heartbeat", "/api/hub/workers/heartbeat"}:
                diag_id, diag_started_at = self._worker_route_diag_start("worker.heartbeat", path)
                if not self._worker_route_enter_or_reject(diag_id, "worker.heartbeat", diag_started_at):
                    return
                try:
                    self._worker_route_diag_step(diag_id, "worker.heartbeat", "read_json.start", diag_started_at)
                    body = self._read_json()
                    worker_id = str(body.get("worker_node_id") or body.get("node_id") or "").strip()
                    worker_instance_id = str(body.get("worker_instance_id") or body.get("connection_id") or "").strip()
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.heartbeat",
                        "read_json.done",
                        diag_started_at,
                        worker_node_id=worker_id,
                        body_keys=sorted(str(key) for key in body.keys()),
                    )
                    if not worker_id:
                        raise ValueError("worker_node_id is required.")
                    capabilities = dict(body.get("capabilities", {})) if isinstance(body.get("capabilities"), dict) else None
                    current_worker = self.server.registry.get_worker(worker_id, worker_instance_id=worker_instance_id)
                    worker_authorization = self._authorize_worker_route(
                        body=body,
                        worker_id=worker_id,
                        current_worker=current_worker,
                    )
                    if worker_authorization:
                        if capabilities is None:
                            capabilities = {}
                        capabilities["wallet_address"] = worker_authorization["wallet_address"]
                        capabilities["credit_wallet"] = worker_authorization["wallet_address"]
                        capabilities["multisession_key_id"] = worker_authorization["multisession_key_id"]
                        capabilities["multisession_key_authorized"] = True
                        capabilities["auth_mode"] = "multisession-wallet"
                    heartbeat_sent_ring = self._payload_has_requested_ring(body, capabilities)
                    if heartbeat_sent_ring:
                        if capabilities is None:
                            capabilities = {}
                        wallet_address = self._wallet_address_from_worker_payload(body, capabilities)
                        if not wallet_address and current_worker is not None:
                            wallet_address = self._wallet_address_from_worker_payload({}, current_worker.capabilities)
                        requested_ring = self._requested_ring_from_worker_payload(body, capabilities, default=None)
                        decision = self.server.ring_admission_config.evaluate(
                            wallet_address=wallet_address,
                            requested_ring=requested_ring,
                        )
                        current_effective_ring = self._current_worker_effective_ring(current_worker)
                        if not decision.ok:
                            self._record_ring_admission_rejection(
                                wallet_address=wallet_address,
                                node_id=worker_id,
                                worker_instance_id=worker_instance_id,
                                requested_ring=decision.requested_ring,
                                minimum_allowed_ring=decision.minimum_allowed_ring,
                                fallback_ring=decision.fallback_ring,
                                error=decision.error or "ring_not_allowed",
                                message=decision.message,
                            )
                            self._send_json(
                                self._ring_admission_rejection_payload(
                                    decision=decision,
                                    wallet_address=wallet_address,
                                    node_id=worker_id,
                                ),
                                status=HTTPStatus.FORBIDDEN,
                            )
                            return
                        if current_effective_ring is not None and requested_ring != current_effective_ring:
                            payload = self._heartbeat_ring_change_rejection_payload(
                                requested_ring=requested_ring,
                                current_effective_ring=current_effective_ring,
                                minimum_allowed_ring=decision.minimum_allowed_ring,
                                wallet_address=wallet_address,
                                node_id=worker_id,
                            )
                            self._record_ring_admission_rejection(
                                wallet_address=wallet_address,
                                node_id=worker_id,
                                worker_instance_id=worker_instance_id,
                                requested_ring=requested_ring,
                                minimum_allowed_ring=decision.minimum_allowed_ring,
                                fallback_ring=current_effective_ring,
                                error="ring_change_requires_reregister",
                                message=str(payload.get("message", "")),
                            )
                            self._send_json(payload, status=HTTPStatus.CONFLICT)
                            return
                        self._apply_accepted_ring_admission(
                            capabilities=capabilities,
                            decision=decision,
                            wallet_address=wallet_address,
                        )
                        self._preserve_registered_ring_capabilities(
                            capabilities=capabilities,
                            current_worker=current_worker,
                        )
                    elif capabilities is not None:
                        self._preserve_registered_ring_capabilities(
                            capabilities=capabilities,
                            current_worker=current_worker,
                        )
                    self._worker_route_diag_step(diag_id, "worker.heartbeat", "registry.heartbeat_worker.start", diag_started_at, worker_node_id=worker_id)
                    worker = self.server.registry.heartbeat_worker(
                        worker_id,
                        status=str(body.get("status", "available")),
                        model=str(body.get("model", "")),
                        models=[str(item) for item in body.get("models", [])] if isinstance(body.get("models"), list) else None,
                        capabilities=capabilities,
                        queue_depth=int(body.get("queue_depth")) if body.get("queue_depth") is not None else None,
                        active_requests=int(body.get("active_requests")) if body.get("active_requests") is not None else None,
                        max_concurrency=int(body.get("max_concurrency")) if body.get("max_concurrency") is not None else None,
                        worker_instance_id=worker_instance_id,
                    )
                    self._worker_route_diag_step(diag_id, "worker.heartbeat", "registry.heartbeat_worker.done", diag_started_at, worker_node_id=worker.node_id)
                    self._worker_route_diag_step(diag_id, "worker.heartbeat", "hub_status.omitted", diag_started_at)
                    self._worker_route_diag_step(diag_id, "worker.heartbeat", "send_json.start", diag_started_at, status=200)
                    self._send_json(self._worker_route_success_payload(worker))
                    self._worker_route_diag_step(diag_id, "worker.heartbeat", "send_json.done", diag_started_at, status=200)
                    return
                except HubCreditAuthorizationError as exc:
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.heartbeat",
                        "auth_error",
                        diag_started_at,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                    return
                except Exception as exc:
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.heartbeat",
                        "exception",
                        diag_started_at,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    raise
                finally:
                    self._worker_route_exit(diag_id, "worker.heartbeat", diag_started_at)
            if path.startswith("/api/hub/v1/workers/") and path.endswith("/heartbeat"):
                diag_id, diag_started_at = self._worker_route_diag_start("worker.heartbeat_by_id", path)
                if not self._worker_route_enter_or_reject(diag_id, "worker.heartbeat_by_id", diag_started_at):
                    return
                try:
                    self._worker_route_diag_step(diag_id, "worker.heartbeat_by_id", "read_json.start", diag_started_at)
                    body = self._read_json()
                    worker_id = path.removeprefix("/api/hub/v1/workers/").removesuffix("/heartbeat").strip("/")
                    worker_instance_id = str(body.get("worker_instance_id") or body.get("connection_id") or worker_id).strip()
                    worker_node_id = str(body.get("worker_node_id") or body.get("node_id") or worker_id).strip()
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.heartbeat_by_id",
                        "read_json.done",
                        diag_started_at,
                        worker_node_id=worker_id,
                        body_keys=sorted(str(key) for key in body.keys()),
                    )
                    if not worker_id or "/" in worker_id:
                        self._worker_route_diag_step(diag_id, "worker.heartbeat_by_id", "send_error.start", diag_started_at, status=404)
                        self.send_error(HTTPStatus.NOT_FOUND)
                        self._worker_route_diag_step(diag_id, "worker.heartbeat_by_id", "send_error.done", diag_started_at, status=404)
                        return
                    capabilities = dict(body.get("capabilities", {})) if isinstance(body.get("capabilities"), dict) else None
                    current_worker = self.server.registry.get_worker(worker_id, worker_instance_id=worker_instance_id)
                    worker_authorization = self._authorize_worker_route(
                        body=body,
                        worker_id=worker_id,
                        current_worker=current_worker,
                    )
                    if worker_authorization:
                        if capabilities is None:
                            capabilities = {}
                        capabilities["wallet_address"] = worker_authorization["wallet_address"]
                        capabilities["credit_wallet"] = worker_authorization["wallet_address"]
                        capabilities["multisession_key_id"] = worker_authorization["multisession_key_id"]
                        capabilities["multisession_key_authorized"] = True
                        capabilities["auth_mode"] = "multisession-wallet"
                    heartbeat_sent_ring = self._payload_has_requested_ring(body, capabilities)
                    if heartbeat_sent_ring:
                        if capabilities is None:
                            capabilities = {}
                        wallet_address = self._wallet_address_from_worker_payload(body, capabilities)
                        if not wallet_address and current_worker is not None:
                            wallet_address = self._wallet_address_from_worker_payload({}, current_worker.capabilities)
                        requested_ring = self._requested_ring_from_worker_payload(body, capabilities, default=None)
                        decision = self.server.ring_admission_config.evaluate(
                            wallet_address=wallet_address,
                            requested_ring=requested_ring,
                        )
                        current_effective_ring = self._current_worker_effective_ring(current_worker)
                        if not decision.ok:
                            self._record_ring_admission_rejection(
                                wallet_address=wallet_address,
                                node_id=worker_id,
                                worker_instance_id=worker_instance_id,
                                requested_ring=decision.requested_ring,
                                minimum_allowed_ring=decision.minimum_allowed_ring,
                                fallback_ring=decision.fallback_ring,
                                error=decision.error or "ring_not_allowed",
                                message=decision.message,
                            )
                            self._send_json(
                                self._ring_admission_rejection_payload(
                                    decision=decision,
                                    wallet_address=wallet_address,
                                    node_id=worker_id,
                                ),
                                status=HTTPStatus.FORBIDDEN,
                            )
                            return
                        if current_effective_ring is not None and requested_ring != current_effective_ring:
                            payload = self._heartbeat_ring_change_rejection_payload(
                                requested_ring=requested_ring,
                                current_effective_ring=current_effective_ring,
                                minimum_allowed_ring=decision.minimum_allowed_ring,
                                wallet_address=wallet_address,
                                node_id=worker_id,
                            )
                            self._record_ring_admission_rejection(
                                wallet_address=wallet_address,
                                node_id=worker_id,
                                worker_instance_id=worker_instance_id,
                                requested_ring=requested_ring,
                                minimum_allowed_ring=decision.minimum_allowed_ring,
                                fallback_ring=current_effective_ring,
                                error="ring_change_requires_reregister",
                                message=str(payload.get("message", "")),
                            )
                            self._send_json(payload, status=HTTPStatus.CONFLICT)
                            return
                        self._apply_accepted_ring_admission(
                            capabilities=capabilities,
                            decision=decision,
                            wallet_address=wallet_address,
                        )
                        self._preserve_registered_ring_capabilities(
                            capabilities=capabilities,
                            current_worker=current_worker,
                        )
                    elif capabilities is not None:
                        self._preserve_registered_ring_capabilities(
                            capabilities=capabilities,
                            current_worker=current_worker,
                        )
                    self._worker_route_diag_step(diag_id, "worker.heartbeat_by_id", "registry.heartbeat_worker.start", diag_started_at, worker_node_id=worker_id)
                    worker = self.server.registry.heartbeat_worker(
                        worker_id,
                        status=str(body.get("status", "available")),
                        model=str(body.get("model", "")),
                        models=[str(item) for item in body.get("models", [])] if isinstance(body.get("models"), list) else None,
                        capabilities=capabilities,
                        queue_depth=int(body.get("queue_depth")) if body.get("queue_depth") is not None else None,
                        active_requests=int(body.get("active_requests")) if body.get("active_requests") is not None else None,
                        max_concurrency=int(body.get("max_concurrency")) if body.get("max_concurrency") is not None else None,
                        worker_instance_id=worker_instance_id,
                    )
                    self._worker_route_diag_step(diag_id, "worker.heartbeat_by_id", "registry.heartbeat_worker.done", diag_started_at, worker_node_id=worker.node_id)
                    self._worker_route_diag_step(diag_id, "worker.heartbeat_by_id", "hub_status.omitted", diag_started_at)
                    self._worker_route_diag_step(diag_id, "worker.heartbeat_by_id", "send_json.start", diag_started_at, status=200)
                    self._send_json(self._worker_route_success_payload(worker))
                    self._worker_route_diag_step(diag_id, "worker.heartbeat_by_id", "send_json.done", diag_started_at, status=200)
                    return
                except HubCreditAuthorizationError as exc:
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.heartbeat_by_id",
                        "auth_error",
                        diag_started_at,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                    return
                except Exception as exc:
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.heartbeat_by_id",
                        "exception",
                        diag_started_at,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    raise
                finally:
                    self._worker_route_exit(diag_id, "worker.heartbeat_by_id", diag_started_at)
            if path in {"/api/hub/v1/workers/poll", "/api/hub/workers/poll"}:
                diag_id, diag_started_at = self._worker_route_diag_start("worker.poll", path)
                if not self._worker_route_enter_or_reject(diag_id, "worker.poll", diag_started_at):
                    return
                try:
                    self._worker_route_diag_step(diag_id, "worker.poll", "read_json.start", diag_started_at)
                    body = self._read_json()
                    worker_id = str(body.get("worker_node_id") or body.get("node_id") or "").strip()
                    worker_instance_id = str(body.get("worker_instance_id") or body.get("connection_id") or worker_id).strip()
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.poll",
                        "read_json.done",
                        diag_started_at,
                        worker_node_id=worker_id,
                        body_keys=sorted(str(key) for key in body.keys()),
                    )
                    if not worker_id:
                        raise ValueError("worker_node_id is required.")
                    current_worker = self.server.registry.get_worker(worker_id, worker_instance_id=worker_instance_id)
                    self._authorize_worker_route(
                        body=body,
                        worker_id=worker_id,
                        current_worker=current_worker,
                    )
                    self._worker_route_diag_step(diag_id, "worker.poll", "dispatcher.poll_worker.start", diag_started_at, worker_node_id=worker_id)
                    response_payload = self.server.dispatcher.poll_worker(
                        worker_node_id=worker_id,
                        worker_instance_id=worker_instance_id,
                        lease_seconds=float(body.get("lease_seconds")) if body.get("lease_seconds") is not None else None,
                    )
                    lease = response_payload.get("lease") if isinstance(response_payload, dict) else None
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.poll",
                        "dispatcher.poll_worker.done",
                        diag_started_at,
                        worker_node_id=worker_id,
                        lease_present=isinstance(lease, dict),
                        request_id=lease.get("request_id") if isinstance(lease, dict) else "",
                        lease_id=lease.get("lease_id") if isinstance(lease, dict) else "",
                    )
                    self._worker_route_diag_step(diag_id, "worker.poll", "send_json.start", diag_started_at, status=200)
                    self._send_json(response_payload)
                    self._worker_route_diag_step(diag_id, "worker.poll", "send_json.done", diag_started_at, status=200)
                    return
                except HubCreditAuthorizationError as exc:
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.poll",
                        "auth_error",
                        diag_started_at,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                    return
                except Exception as exc:
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.poll",
                        "exception",
                        diag_started_at,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    raise
                finally:
                    self._worker_route_exit(diag_id, "worker.poll", diag_started_at)
            if path in {"/api/hub/v1/workers/stream-events", "/api/hub/workers/stream-events"}:
                diag_id, diag_started_at = self._worker_route_diag_start("worker.stream_events", path)
                if not self._worker_route_enter_or_reject(diag_id, "worker.stream_events", diag_started_at):
                    return
                try:
                    self._worker_route_diag_step(diag_id, "worker.stream_events", "read_json.start", diag_started_at)
                    body = self._read_json()
                    worker_id = str(body.get("worker_node_id") or body.get("node_id") or "")
                    worker_instance_id = str(body.get("worker_instance_id") or body.get("connection_id") or worker_id).strip()
                    stream_event = body.get("event") if isinstance(body.get("event"), dict) else body.get("stream_event")
                    if not isinstance(stream_event, dict):
                        raise ValueError("event object is required.")
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.stream_events",
                        "read_json.done",
                        diag_started_at,
                        worker_node_id=worker_id,
                        request_id=str(body.get("request_id", "")),
                        lease_id=str(body.get("lease_id", "")),
                        stream_event=str(stream_event.get("event") or stream_event.get("type") or stream_event.get("stream_event") or ""),
                        body_keys=sorted(str(key) for key in body.keys()),
                    )
                    current_worker = self.server.registry.get_worker(worker_id, worker_instance_id=worker_instance_id)
                    self._authorize_worker_route(
                        body=body,
                        worker_id=worker_id,
                        current_worker=current_worker,
                    )
                    self._worker_route_diag_step(diag_id, "worker.stream_events", "dispatcher.submit_worker_stream_event.start", diag_started_at, worker_node_id=worker_id)
                    response_payload = self.server.dispatcher.submit_worker_stream_event(
                        worker_node_id=worker_id,
                        worker_instance_id=worker_instance_id,
                        request_id=str(body.get("request_id", "")),
                        lease_id=str(body.get("lease_id", "")),
                        event=stream_event,
                    )
                    self._worker_route_diag_step(diag_id, "worker.stream_events", "dispatcher.submit_worker_stream_event.done", diag_started_at, worker_node_id=worker_id)
                    self._worker_route_diag_step(diag_id, "worker.stream_events", "send_json.start", diag_started_at, status=200)
                    self._send_json(response_payload)
                    self._worker_route_diag_step(diag_id, "worker.stream_events", "send_json.done", diag_started_at, status=200)
                    return
                except HubCreditAuthorizationError as exc:
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.stream_events",
                        "auth_error",
                        diag_started_at,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                    return
                except Exception as exc:
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.stream_events",
                        "exception",
                        diag_started_at,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    raise
                finally:
                    self._worker_route_exit(diag_id, "worker.stream_events", diag_started_at)
            if path in {"/api/hub/v1/workers/results", "/api/hub/workers/results"}:
                diag_id, diag_started_at = self._worker_route_diag_start("worker.results", path)
                if not self._worker_route_enter_or_reject(diag_id, "worker.results", diag_started_at):
                    return
                try:
                    self._worker_route_diag_step(diag_id, "worker.results", "read_json.start", diag_started_at)
                    body = self._read_json()
                    worker_id = str(body.get("worker_node_id") or body.get("node_id") or "")
                    worker_instance_id = str(body.get("worker_instance_id") or body.get("connection_id") or worker_id).strip()
                    result = body.get("result") if isinstance(body.get("result"), dict) else body.get("response")
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.results",
                        "read_json.done",
                        diag_started_at,
                        worker_node_id=worker_id,
                        request_id=str(body.get("request_id", "")),
                        lease_id=str(body.get("lease_id", "")),
                        result_present=isinstance(result, dict),
                        body_keys=sorted(str(key) for key in body.keys()),
                    )
                    if not isinstance(result, dict):
                        raise ValueError("result or response object is required.")
                    current_worker = self.server.registry.get_worker(worker_id, worker_instance_id=worker_instance_id)
                    self._authorize_worker_route(
                        body=body,
                        worker_id=worker_id,
                        current_worker=current_worker,
                    )
                    self._worker_route_diag_step(diag_id, "worker.results", "dispatcher.submit_worker_result.start", diag_started_at, worker_node_id=worker_id)
                    response_payload = self.server.dispatcher.submit_worker_result(
                        worker_node_id=worker_id,
                        worker_instance_id=worker_instance_id,
                        request_id=str(body.get("request_id", "")),
                        lease_id=str(body.get("lease_id", "")),
                        result=result,
                    )
                    self._worker_route_diag_step(diag_id, "worker.results", "dispatcher.submit_worker_result.done", diag_started_at, worker_node_id=worker_id)
                    self._worker_route_diag_step(diag_id, "worker.results", "send_json.start", diag_started_at, status=200)
                    self._send_json(response_payload)
                    self._worker_route_diag_step(diag_id, "worker.results", "send_json.done", diag_started_at, status=200)
                    return
                except HubCreditAuthorizationError as exc:
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.results",
                        "auth_error",
                        diag_started_at,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                    return
                except Exception as exc:
                    self._worker_route_diag_step(
                        diag_id,
                        "worker.results",
                        "exception",
                        diag_started_at,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    raise
                finally:
                    self._worker_route_exit(diag_id, "worker.results", diag_started_at)
            if path in {"/api/hub/upstreams/register", "/api/hub/v1/upstreams/register"}:
                body = self._read_json()
                upstream = self.server.registry.register_upstream_hub(
                    node_id=str(body.get("node_id", "")),
                    endpoint=str(body.get("endpoint", "")),
                    credits_per_request=_hub_credit_public_value_from_wei(
                        _hub_credit_wei_from_explicit_or_decimal(
                            body.get("credits_per_request_wei"),
                            body.get("credits_per_request", self.server.config.hub_credits_per_request),
                            "1",
                            minimum_wei=1,
                        )
                    ),
                )
                self.server.energy_ledger.register_node(upstream.node_id, "upstream-hub", upstream.endpoint)
                self._send_json({"ok": True, "upstream_hub": upstream.as_dict(), "hub": self.server.registry.status()})
                return
            if path == "/api/hub/sessions/start":
                body = self._read_json()
                requester_public_key = str(body.get("requester_public_key", ""))
                if not requester_public_key:
                    raise ValueError("requester_public_key is required.")
                self._send_json(
                    self.server.dispatcher.start_secure_session(
                        requester_public_key=requester_public_key,
                        model=str(body.get("model", self.server.config.model)),
                        client_node_id=str(body.get("client_node_id", self.server.config.hub_client_node_id)),
                        hop_count=int(body.get("hop_count", 0) or 0),
                    )
                )
                return
            if path == "/api/hub/sessions/chat":
                body = self._read_json()
                envelope = body.get("envelope")
                if not isinstance(envelope, dict):
                    raise ValueError("encrypted envelope is required.")
                self._send_json(
                    self.server.dispatcher.secure_chat(
                        session_id=str(body.get("session_id", "")),
                        request_id=str(body.get("request_id", "")),
                        envelope=envelope,
                    )
                )
                return
            if path in {"/api/hub/payouts/claim", "/api/hub/v1/payouts/claim"}:
                body = self._read_json()
                node_id = str(body.get("node_id", ""))
                exact_payouts = self._exact_payouts_requested(body=body)
                precision = self._worker_settlement_precision_places(node_id, body.get("precision_places"))
                self._send_json(
                    self.server.energy_ledger.claim_payouts(
                        node_id=node_id,
                        memo=str(body.get("memo", "")),
                        exact=exact_payouts,
                        precision_places=precision,
                    )
                )
                return
            if path in {"/api/hub/v1/workers/claims", "/api/hub/v1/credits/worker-claims/record"}:
                body = self._read_json()
                raw_earning_ids = body.get("earning_ids")
                earning_ids = [str(item) for item in raw_earning_ids] if isinstance(raw_earning_ids, list) else None
                result = self.server.credit_ledger.record_worker_claim(
                    worker_node_id=str(body.get("worker_node_id") or body.get("node_id") or ""),
                    earning_ids=earning_ids,
                    claim_credits=int(body["claim_credits"]) if body.get("claim_credits") is not None else None,
                    idempotency_key=str(body.get("idempotency_key", "")),
                    memo=str(body.get("memo", "")),
                    metadata=dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {},
                )
                self._send_json(result)
                return
            if path in {"/api/hub/v1/workers/settlements/batches", "/api/hub/v1/credits/worker-settlements/batches"}:
                body = self._read_json()
                worker_node_id = str(body.get("worker_node_id") or body.get("node_id") or "")
                raw_claim_ids = body.get("claim_ids")
                claim_ids = [str(item) for item in raw_claim_ids] if isinstance(raw_claim_ids, list) else None
                precision = self._worker_settlement_precision_places(worker_node_id, body.get("precision_places"))
                result = self.server.credit_ledger.create_worker_settlement_batch(
                    worker_node_id=worker_node_id,
                    claim_ids=claim_ids,
                    precision_places=precision,
                    idempotency_key=str(body.get("idempotency_key", "")),
                    bridge_account_id=str(body.get("bridge_account_id", "bridge-worker-payout-dust")),
                    metadata=dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {},
                )
                self._send_json(result)
                return
            if path in {
                "/api/hub/v1/workers/settlements/batches/settle",
                "/api/hub/v1/workers/settlements/settle",
                "/api/hub/v1/credits/worker-settlements/batches/settle",
            }:
                body = self._read_json()
                result = self.server.credit_ledger.settle_worker_settlement_batch(
                    batch_id=str(body.get("batch_id", "")),
                    settlement_reference=str(body.get("settlement_reference", body.get("reference", ""))),
                    settlement_tx_hash=str(body.get("settlement_tx_hash", body.get("tx_hash", ""))),
                    payout_rail=str(body.get("payout_rail", "")),
                    operator_id=str(body.get("operator_id", "")),
                    settlement_proof=dict(body.get("settlement_proof", body.get("proof", {}))) if isinstance(body.get("settlement_proof", body.get("proof", {})), dict) else {},
                    idempotency_key=str(body.get("idempotency_key", "")),
                    metadata=dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {},
                )
                self._send_json(result)
                return
            if path == "/api/hub/v1/credits/wallet-funding/complete":
                body = self._read_json()
                self._send_json(self.server.credit_bridge_completion.complete_wallet_funding_deposit(body))
                return
            if path == "/api/hub/v1/credits/wallet-funding/import":
                body = self._read_json()
                self._send_json(self.server.credit_indexer.import_wallet_funding(body))
                return
            if path in {"/api/hub/v1/credits/deposits/import", "/api/hub/v1/credits/purchases/import"}:
                body = self._read_json()
                self._send_json(self.server.credit_indexer.import_deposit(body))
                return
            if path == "/api/hub/v1/credits/bridge-reconciliation/record":
                body = self._read_json()
                result = self.server.credit_ledger.record_bridge_reconciliation(
                    account_id=str(body.get("account_id", "")),
                    rectified_credits=int(body.get("rectified_credits", 0) or 0),
                    withdrawn_credits=int(body.get("withdrawn_credits", 0) or 0),
                    rectification_id=str(body.get("rectification_id", "")),
                    withdrawal_id=str(body.get("withdrawal_id", "")),
                    recipient_address=str(body.get("recipient_address", "")),
                    memo=str(body.get("memo", "")),
                    metadata=dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {},
                )
                self._send_json(result)
                return
            if path == "/api/hub/v1/credits/admin/issue":
                body = self._read_json()
                result = self.server.credit_ledger.issue(
                    account_id=str(body.get("account_id", self.server.config.hub_client_node_id)),
                    credits=int(body.get("credits", 0) or 0),
                    memo=str(body.get("memo", "")),
                    owner_address=str(body.get("owner_address", "")),
                    metadata=dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {},
                )
                self._send_json(result)
                return
            if path == "/api/hub/v1/bridge/mock-chain/mint":
                if not hasattr(self.server.credit_ledger, "mock_chain_mint"):
                    self._send_json({"ok": False, "error": "mock chain bridge is not available on this ledger."}, status=HTTPStatus.NOT_IMPLEMENTED)
                    return
                body = self._read_json()
                self._send_json(
                    self.server.credit_ledger.mock_chain_mint(
                        wallet_address=str(body.get("wallet_address", "")),
                        credits=int(body.get("credits", 0) or 0),
                        idempotency_key=str(body.get("idempotency_key", "")),
                        memo=str(body.get("memo", "")),
                        metadata=dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {},
                    )
                )
                return
            if path == "/api/hub/v1/bridge/deposits":
                if not hasattr(self.server.credit_ledger, "create_bridge_deposit"):
                    self._send_json({"ok": False, "error": "mock chain bridge is not available on this ledger."}, status=HTTPStatus.NOT_IMPLEMENTED)
                    return
                body = self._read_json()
                self._send_json(
                    self.server.credit_ledger.create_bridge_deposit(
                        wallet_address=str(body.get("wallet_address", "")),
                        account_id=str(body.get("account_id", self.server.config.hub_client_node_id)),
                        credits=int(body.get("credits", 0) or 0),
                        idempotency_key=str(body.get("idempotency_key", "")),
                        memo=str(body.get("memo", "")),
                        metadata=dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {},
                    )
                )
                return
            if path == "/api/hub/v1/bridge/deposits/confirm":
                if not hasattr(self.server.credit_ledger, "confirm_bridge_deposit"):
                    self._send_json({"ok": False, "error": "mock chain bridge is not available on this ledger."}, status=HTTPStatus.NOT_IMPLEMENTED)
                    return
                body = self._read_json()
                deposit_id = str(body.get("deposit_id", ""))
                metadata = dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {}
                metadata = self._with_hub_bridge_backend_deposit_metadata(deposit_id=deposit_id, metadata=metadata)
                self._send_json(
                    self.server.credit_ledger.confirm_bridge_deposit(
                        deposit_id=deposit_id,
                        metadata=metadata,
                    )
                )
                return
            if path == "/api/hub/v1/bridge/payouts":
                if not hasattr(self.server.credit_ledger, "request_bridge_payout"):
                    self._send_json({"ok": False, "error": "mock chain bridge is not available on this ledger."}, status=HTTPStatus.NOT_IMPLEMENTED)
                    return
                body = self._read_json()
                wallet_address = normalize_address(str(body.get("wallet_address", "")))
                worker_node_id = str(body.get("worker_node_id") or body.get("node_id") or "")
                if wallet_address:
                    status = self.server.registry.status()
                    workers = [item for item in status.get("workers", []) if isinstance(item, dict)]
                    wallet_workers = []
                    for worker in workers:
                        caps = dict(worker.get("capabilities", {})) if isinstance(worker.get("capabilities"), dict) else {}
                        worker_wallet = normalize_address(str(worker.get("wallet_address") or caps.get("wallet_address") or ""))
                        if worker_wallet == wallet_address:
                            wallet_workers.append(worker)
                    active_workers = [
                        worker
                        for worker in wallet_workers
                        if int(worker.get("active_requests", 0) or 0) > 0
                    ]
                    if active_workers:
                        self._send_json(
                            {
                                "ok": False,
                                "error": "wallet has active worker leases; payout requires a quiet wallet",
                                "error_type": "wallet_active_worker_leases",
                                "wallet_address": wallet_address,
                                "active_worker_node_ids": [str(worker.get("node_id", "")) for worker in active_workers],
                                "active_worker_count": len(active_workers),
                            },
                            status=HTTPStatus.CONFLICT,
                        )
                        return
                self._send_json(
                    self.server.credit_ledger.request_bridge_payout(
                        wallet_address=wallet_address,
                        account_id=str(body.get("account_id", "")),
                        worker_node_id=worker_node_id,
                        credits=int(body.get("credits", 0) or 0),
                        idempotency_key=str(body.get("idempotency_key", "")),
                        memo=str(body.get("memo", "")),
                        metadata=dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {},
                    )
                )
                return
            if path == "/api/hub/v1/bridge/payouts/confirm":
                if not hasattr(self.server.credit_ledger, "confirm_bridge_payout"):
                    self._send_json({"ok": False, "error": "mock chain bridge is not available on this ledger."}, status=HTTPStatus.NOT_IMPLEMENTED)
                    return
                body = self._read_json()
                payout_id = str(body.get("payout_id", ""))
                metadata = dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {}
                metadata = self._with_hub_bridge_backend_payout_metadata(payout_id=payout_id, metadata=metadata)
                self._send_json(
                    self.server.credit_ledger.confirm_bridge_payout(
                        payout_id=payout_id,
                        metadata=metadata,
                    )
                )
                return
            if path == "/api/hub/v1/bridge/payouts/fail":
                if not hasattr(self.server.credit_ledger, "fail_bridge_payout"):
                    self._send_json({"ok": False, "error": "mock chain bridge is not available on this ledger."}, status=HTTPStatus.NOT_IMPLEMENTED)
                    return
                body = self._read_json()
                self._send_json(
                    self.server.credit_ledger.fail_bridge_payout(
                        payout_id=str(body.get("payout_id", "")),
                        reason=str(body.get("reason", "")),
                        metadata=dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {},
                    )
                )
                return
            if path in {
                "/api/hub/v1/workers/settlements/chain-executions",
                "/api/hub/v1/workers/settlements/batches/chain-execution",
                "/api/hub/v1/credits/worker-settlements/chain-executions",
            }:
                body = self._read_json()
                result = self.server.credit_ledger.record_worker_settlement_chain_execution(
                    batch_id=str(body.get("batch_id", "")),
                    chain_id=int(body.get("chain_id", 0) or 0),
                    contract_address=str(body.get("contract_address", "")),
                    recipient_address=str(body.get("recipient_address", body.get("worker_payout_address", ""))),
                    payout_units_executed=int(body.get("payout_units_executed", body.get("executed_credits", 0)) or 0),
                    settlement_tx_hash=str(body.get("settlement_tx_hash", body.get("tx_hash", ""))),
                    proposal_id=str(body.get("proposal_id", "")),
                    block_number=int(body.get("block_number", 0) or 0) if body.get("block_number") is not None else None,
                    payout_rail=str(body.get("payout_rail", "xlag-bridge-reserve")),
                    operator_id=str(body.get("operator_id", "")),
                    settlement_reference=str(body.get("settlement_reference", body.get("reference", ""))),
                    settlement_proof=dict(body.get("settlement_proof", body.get("proof", {}))) if isinstance(body.get("settlement_proof", body.get("proof", {})), dict) else {},
                    idempotency_key=str(body.get("idempotency_key", "")),
                    metadata=dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {},
                )
                self._send_json(result)
                return
            if path in {
                "/api/hub/v1/workers/settlements/proofs",
                "/api/hub/v1/workers/settlements/batches/proof",
                "/api/hub/v1/credits/worker-settlements/proofs",
            }:
                body = self._read_json()
                result = self.server.credit_ledger.record_worker_settlement_proof(
                    batch_id=str(body.get("batch_id", "")),
                    settlement_reference=str(body.get("settlement_reference", body.get("reference", ""))),
                    settlement_tx_hash=str(body.get("settlement_tx_hash", body.get("tx_hash", ""))),
                    payout_rail=str(body.get("payout_rail", "operator-manual")),
                    operator_id=str(body.get("operator_id", "")),
                    settlement_proof=dict(body.get("settlement_proof", body.get("proof", {}))) if isinstance(body.get("settlement_proof", body.get("proof", {})), dict) else {},
                    idempotency_key=str(body.get("idempotency_key", "")),
                    metadata=dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {},
                )
                self._send_json(result)
                return
            if path == "/api/hub/v1/requests/quote":
                body = self._read_json()
                hub_request = HubAIRequest.from_payload(
                    body,
                    default_model=self.server.config.model,
                    default_client_node_id=self.server.config.hub_client_node_id,
                )
                self._send_json(self.server.dispatcher.quote(hub_request))
                return
            if path == "/api/hub/v1/requests":
                body = self._read_json()
                incoming_metadata = dict(body.get("metadata", {})) if isinstance(body.get("metadata"), dict) else {}
                execution_mode_hint = str(body.get("execution_mode") or incoming_metadata.get("execution_mode") or "").strip().lower()
                is_worker_pull = (
                    execution_mode_hint in {"worker_pull_v0", "worker-pull-v0", "worker_pull", "worker-pull"}
                    or incoming_metadata.get("worker_pull_v0") is True
                )
                try:
                    body, _authorized_metadata = self._apply_request_multisession_authorization(
                        body=body,
                        metadata=incoming_metadata,
                        required=bool(self.server.config.hub_require_multisession_auth and is_worker_pull),
                    )
                except HubCreditAuthorizationError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                    return
                hub_request = HubAIRequest.from_payload(
                    body,
                    default_model=self.server.config.model,
                    default_client_node_id=self.server.config.hub_client_node_id,
                )
                metadata = dict(hub_request.metadata)
                execution_mode = str(body.get("execution_mode") or metadata.get("execution_mode") or "").strip().lower()
                if execution_mode in {"worker_pull_v0", "worker-pull-v0", "worker_pull", "worker-pull"} or metadata.get("worker_pull_v0") is True:
                    status_payload = self.server.dispatcher.submit_worker_pull(hub_request)
                else:
                    status_payload = self.server.dispatcher.submit(hub_request)
                if hasattr(status_payload, "as_dict"):
                    status_payload = status_payload.as_dict()
                self._send_json({"ok": True, "request": status_payload})
                return
            if path.startswith("/api/hub/v1/requests/") and path.endswith("/feedback"):
                request_id = path.removeprefix("/api/hub/v1/requests/").removesuffix("/feedback").strip("/")
                if not request_id or "/" in request_id:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                body = self._read_json()
                try:
                    self._send_json(self.server.dispatcher.submit_request_feedback(request_id, body))
                except PermissionError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if path.startswith("/api/hub/v1/requests/") and path.endswith("/cancel"):
                request_id = path.removeprefix("/api/hub/v1/requests/").removesuffix("/cancel").strip("/")
                if not request_id or "/" in request_id:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_json({"ok": True, "request": self.server.dispatcher.cancel_request(request_id)})
                return
            if path == "/api/hub/chat":
                body = self._read_json()
                if self.server.config.hub_high_security and body.get("high_security") is not False:
                    raise ValueError(
                        "High-security hub mode is enabled; use /api/hub/sessions/start and encrypted envelopes, "
                        "or send high_security=false for an explicit legacy plaintext request."
                    )
                messages_payload = body.get("messages")
                if not messages_payload and body.get("prompt"):
                    messages_payload = [{"role": "user", "content": str(body.get("prompt", ""))}]
                if not isinstance(messages_payload, list):
                    raise ValueError("messages must be a list, or prompt must be supplied.")
                messages = [chat_message_from_dict(item) for item in messages_payload if isinstance(item, dict)]
                response = self.server.dispatcher.chat(
                    messages=messages,
                    model=str(body.get("model", self.server.config.model)),
                    client_node_id=str(body.get("client_node_id", self.server.config.hub_client_node_id)),
                    hop_count=int(body.get("hop_count", 0) or 0),
                )
                self._send_json(
                    {
                        "content": response.content,
                        "provider": response.provider,
                        "model": response.model,
                        "metadata": response.metadata,
                    }
                )
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


class HubWorkerHttpServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        config: MainComputerConfig,
        chat_fn: Callable[[Sequence[ChatMessage]], ChatResponse],
        *,
        verbose: bool = True,
    ) -> None:
        super().__init__(server_address, HubWorkerHandler)
        self.verbose = verbose
        self.config = config
        self.chat_fn = chat_fn
        self.secure_sessions: dict[str, dict[str, Any]] = {}
        self.session_lock = threading.Lock()


class HubWorkerHandler(_JsonHandler):
    server: HubWorkerHttpServer

    def do_GET(self) -> None:
        if self.path == "/api/hub/worker/status":
            self._send_json(
                {
                    "ok": True,
                    "node_id": self.server.config.hub_worker_node_id,
                    "model": self.server.config.model,
                    "provider": self.server.config.provider,
                    "credits_per_request": self.server.config.hub_credits_per_request,
                    "security": {
                        "high_security_default": self.server.config.hub_high_security,
                        "encryption_profile": HUB_SECURITY_PROFILE,
                    },
                }
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == HUB_WORKER_SESSION_START_PATH:
                body = self._read_json()
                session_id = str(body.get("session_id", "")).strip()
                request_id = str(body.get("request_id", "")).strip()
                requester_public_key = str(body.get("requester_public_key", "")).strip()
                if not session_id or not request_id or not requester_public_key:
                    raise ValueError("session_id, request_id, and requester_public_key are required.")
                keypair = generate_hub_session_keypair()
                shared_key = derive_hub_session_key(
                    private_key=keypair.private_key,
                    peer_public_key=requester_public_key,
                    session_id=session_id,
                )
                with self.server.session_lock:
                    self.server.secure_sessions[session_id] = {
                        "request_id": request_id,
                        "key": shared_key,
                        "created_at": _utc_now(),
                        "energy": body.get("energy", {}),
                    }
                self._send_json(
                    {
                        "ok": True,
                        "session_id": session_id,
                        "request_id": request_id,
                        "worker_public_key": keypair.public_key,
                        "encryption": {
                            "profile": HUB_SECURITY_PROFILE,
                            "temporary_public_keys": True,
                            "hub_blind": True,
                        },
                    }
                )
                return
            if parsed.path == HUB_WORKER_SESSION_CHAT_PATH:
                body = self._read_json()
                session_id = str(body.get("session_id", "")).strip()
                request_id = str(body.get("request_id", "")).strip()
                envelope = body.get("envelope")
                if not isinstance(envelope, dict):
                    raise ValueError("encrypted envelope is required.")
                with self.server.session_lock:
                    session = dict(self.server.secure_sessions.get(session_id, {}))
                if not session:
                    raise ValueError("Unknown or expired worker session.")
                if request_id and str(session.get("request_id", "")) != request_id:
                    raise ValueError("Worker session request id mismatch.")
                key = session["key"]
                request_aad = {"session_id": session_id, "request_id": request_id, "direction": "request"}
                secure_payload = decrypt_hub_envelope(envelope, key=key, aad=request_aad)
                messages_payload = secure_payload.get("messages")
                if not isinstance(messages_payload, list):
                    raise ValueError("messages must be a list.")
                messages = [chat_message_from_dict(item) for item in messages_payload if isinstance(item, dict)]
                response = self.server.chat_fn(messages)
                metadata = dict(response.metadata)
                metadata["hub_worker"] = {
                    "request_id": request_id,
                    "node_id": self.server.config.hub_worker_node_id,
                    "energy": session.get("energy", {}),
                    "security_mode": "high-security",
                    "hub_blind": True,
                    "encryption_profile": HUB_SECURITY_PROFILE,
                }
                response_payload = {
                    "content": response.content,
                    "provider": response.provider,
                    "model": response.model,
                    "metadata": metadata,
                }
                response_aad = {"session_id": session_id, "request_id": request_id, "direction": "response"}
                self._send_json(
                    {
                        "ok": True,
                        "session_id": session_id,
                        "request_id": request_id,
                        "response_envelope": encrypt_hub_envelope(response_payload, key=key, aad=response_aad),
                    }
                )
                return
            if parsed.path == HUB_WORKER_CHAT_PATH:
                body = self._read_json()
                messages_payload = body.get("messages")
                if not isinstance(messages_payload, list):
                    raise ValueError("messages must be a list.")
                messages = [chat_message_from_dict(item) for item in messages_payload if isinstance(item, dict)]
                response = self.server.chat_fn(messages)
                metadata = dict(response.metadata)
                metadata["hub_worker"] = {
                    "request_id": body.get("request_id"),
                    "node_id": self.server.config.hub_worker_node_id,
                    "energy": body.get("energy", {}),
                    "security_mode": "legacy-plaintext",
                }
                self._send_json(
                    {
                        "content": response.content,
                        "provider": response.provider,
                        "model": response.model,
                        "metadata": metadata,
                    }
                )
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


def register_worker_with_hub(
    *,
    hub_url: str,
    node_id: str,
    endpoint: str,
    model: str = "",
    models: Sequence[str] | None = None,
    credits_per_request: Any = 1,
    timeout_s: float = 10.0,
    allow_insecure_dev_network: bool = False,
    assigned_ring: int | None = None,
    execution_mode: str | None = None,
    pricing: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
    worker_instance_id: str = "",
) -> dict[str, Any]:
    _require_allowed_transport(hub_url, role="Hub", allow_insecure_dev_network=allow_insecure_dev_network)
    _require_allowed_transport(endpoint, role="Worker", allow_insecure_dev_network=allow_insecure_dev_network)
    credit_price_wei = _hub_credit_wei_from_value(credits_per_request, "1", minimum_wei=1)
    payload: dict[str, Any] = {
        "node_id": node_id,
        "endpoint": endpoint,
        "model": model,
        "credits_per_request": _hub_credit_public_value_from_wei(credit_price_wei),
        "credits_per_request_wei": str(credit_price_wei),
    }
    clean_models = [str(item).strip() for item in (models or []) if str(item).strip()]
    if model and model not in clean_models:
        clean_models.insert(0, str(model))
    if clean_models:
        payload["models"] = clean_models
    if worker_instance_id:
        payload["worker_instance_id"] = worker_instance_id
    if assigned_ring is not None:
        payload["assigned_ring"] = int(assigned_ring)
    if execution_mode:
        payload["execution_mode"] = str(execution_mode)
    if pricing is not None:
        payload["pricing"] = dict(pricing)
    if capabilities is not None:
        payload["capabilities"] = dict(capabilities)
    url = hub_url.rstrip("/") + "/api/hub/v1/workers/register"
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_hub_worker_api_headers(json_body=True),
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(1.0, float(timeout_s or 10.0))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(_hub_worker_http_error_message(url, exc, body)) from exc
    except URLError as exc:
        raise RuntimeError(f"Hub request failed for {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Hub returned a non-object registration response.")
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data


def _post_hub_worker_json(
    *,
    hub_url: str,
    path: str,
    payload: dict[str, Any],
    timeout_s: float,
    allow_insecure_dev_network: bool = False,
) -> dict[str, Any]:
    _require_allowed_transport(hub_url, role="Hub", allow_insecure_dev_network=allow_insecure_dev_network)
    url = hub_url.rstrip("/") + path
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_hub_worker_api_headers(json_body=True),
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(1.0, float(timeout_s or 10.0))) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(_hub_worker_http_error_message(url, exc, body)) from exc
    except URLError as exc:
        raise RuntimeError(f"Hub request failed for {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Hub returned a non-object response.")
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data


def _worker_pull_response_payload(
    *,
    chat_fn: Callable[[Sequence[ChatMessage]], ChatResponse],
    lease: dict[str, Any],
) -> dict[str, Any]:
    messages_payload = lease.get("messages", [])
    messages = [
        chat_message_from_dict(item)
        for item in messages_payload
        if isinstance(item, dict)
    ]
    if not messages:
        messages = [ChatMessage(role="user", content=str(lease.get("prompt") or ""))]
    response = chat_fn(messages)
    metadata = dict(response.metadata) if isinstance(response.metadata, dict) else {}
    metadata.setdefault("worker_pull_v0", True)
    return {
        "status": "success",
        "response": {
            "content": response.content,
            "provider": response.provider,
            "model": response.model,
            "metadata": metadata,
        },
    }


def serve_hub_worker_pull(
    config: MainComputerConfig,
    chat_fn: Callable[[Sequence[ChatMessage]], ChatResponse],
    *,
    hub_url: str | None = None,
    public_endpoint: str | None = None,
    assigned_ring: int = 3,
    execution_mode: str = PHASE9_EXECUTION_MODE,
    poll_interval_s: float = 2.0,
    heartbeat_interval_s: float = 30.0,
    lease_seconds: float | None = None,
    verbose: bool = True,
    max_requests: int | None = None,
) -> None:
    """Run a foreground worker-pull loop that lets a local provider service hub requests."""

    clean_hub_url = (hub_url or config.hub_url).rstrip("/")
    clean_worker_instance_id = config.hub_worker_node_id
    endpoint = (
        public_endpoint
        or config.hub_worker_endpoint
        or f"https://worker-pull.main-computer.local/{config.hub_worker_node_id}"
    ).rstrip("/")
    credit_price_wei = _hub_credit_wei_from_value(config.hub_credits_per_request, "1", minimum_wei=1)
    pricing = {
        "pricing_type": PHASE9_PRICING_TYPE,
        "credits_per_request": _hub_credit_public_value_from_wei(credit_price_wei),
        "credits_per_request_wei": str(credit_price_wei),
        "unit": "compute_credit",
        "execution_mode": execution_mode,
    }
    capabilities = {
        "provider": config.provider,
        "worker_pull_v0": True,
        "assigned_ring": int(assigned_ring),
        "requested_ring": int(assigned_ring),
        "execution_mode": execution_mode,
    }
    report = register_worker_with_hub(
        hub_url=clean_hub_url,
        node_id=config.hub_worker_node_id,
        endpoint=endpoint,
        model=config.model,
        models=[config.model],
        credits_per_request=config.hub_credits_per_request,
        timeout_s=10.0,
        allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
        assigned_ring=int(assigned_ring),
        execution_mode=execution_mode,
        pricing=pricing,
        capabilities=capabilities,
        worker_instance_id=clean_worker_instance_id,
    )
    if verbose:
        worker_payload = report.get("worker", {}) if isinstance(report.get("worker"), dict) else {}
        offer_payload = worker_payload.get("offer", {}) if isinstance(worker_payload.get("offer"), dict) else {}
        print(
            "Registered worker-pull local AI "
            f"{config.hub_worker_node_id} on ring {int(assigned_ring)} "
            f"for model {config.model} at {clean_hub_url}"
        )
        if offer_payload:
            print(f"Advertised worker offer: {offer_payload.get('offer_id', '')} price={offer_payload.get('credits_per_request_display', offer_payload.get('credits_per_request', ''))}")
        print("Polling for worker-pull leases. Leave this window open while the smoke command runs.")

    poll_sleep = max(0.1, float(poll_interval_s or 2.0))
    heartbeat_every = max(1.0, float(heartbeat_interval_s or 30.0))
    processed = 0
    next_heartbeat_at = 0.0
    try:
        while True:
            now = time.monotonic()
            if now >= next_heartbeat_at:
                _post_hub_worker_json(
                    hub_url=clean_hub_url,
                    path="/api/hub/v1/workers/heartbeat",
                    payload={
                        "worker_node_id": config.hub_worker_node_id,
                        "worker_instance_id": clean_worker_instance_id,
                        "status": "available",
                        "model": config.model,
                        "models": [config.model],
                        "active_requests": 0,
                        "queue_depth": 0,
                        "capabilities": capabilities,
                    },
                    timeout_s=min(10.0, config.hub_timeout_s),
                    allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
                )
                next_heartbeat_at = now + heartbeat_every

            poll_payload: dict[str, Any] = {
                "worker_node_id": config.hub_worker_node_id,
                "worker_instance_id": clean_worker_instance_id,
            }
            if lease_seconds is not None:
                poll_payload["lease_seconds"] = float(lease_seconds)
            poll = _post_hub_worker_json(
                hub_url=clean_hub_url,
                path="/api/hub/v1/workers/poll",
                payload=poll_payload,
                timeout_s=min(10.0, config.hub_timeout_s),
                allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
            )
            lease = poll.get("lease")
            if not isinstance(lease, dict):
                time.sleep(poll_sleep)
                continue

            request_id = str(lease.get("request_id", ""))
            lease_id = str(lease.get("lease_id", ""))
            if verbose:
                print(f"Accepted worker-pull lease {lease_id} for request {request_id}")
            _post_hub_worker_json(
                hub_url=clean_hub_url,
                path="/api/hub/v1/workers/heartbeat",
                payload={
                    "worker_node_id": config.hub_worker_node_id,
                    "worker_instance_id": clean_worker_instance_id,
                    "status": "busy",
                    "model": config.model,
                    "models": [config.model],
                    "active_requests": 1,
                    "queue_depth": 0,
                    "capabilities": capabilities,
                },
                timeout_s=min(10.0, config.hub_timeout_s),
                allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
            )
            try:
                result = _worker_pull_response_payload(chat_fn=chat_fn, lease=lease)
            except Exception as exc:
                result = {
                    "status": "failed",
                    "error": str(exc),
                    "message": str(exc),
                }
            completed = _post_hub_worker_json(
                hub_url=clean_hub_url,
                path="/api/hub/v1/workers/results",
                payload={
                    "worker_node_id": config.hub_worker_node_id,
                    "worker_instance_id": clean_worker_instance_id,
                    "request_id": request_id,
                    "lease_id": lease_id,
                    "result": result,
                },
                timeout_s=max(10.0, config.hub_timeout_s),
                allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
            )
            processed += 1
            if verbose:
                request_payload = completed.get("request", {}) if isinstance(completed.get("request"), dict) else {}
                print(f"Submitted result for {request_id}: state={request_payload.get('state', '') or completed.get('state', '')}")
            next_heartbeat_at = 0.0
            if max_requests is not None and processed >= int(max_requests):
                return
    except KeyboardInterrupt:
        print("\nHub worker-pull loop stopped.")



def serve_hub(config: MainComputerConfig, host: str = "127.0.0.1", port: int = DEFAULT_HUB_PORT, *, verbose: bool = True) -> None:
    server = HubHttpServer((host, port), config, verbose=verbose)
    scheme_note = "https required for remote peers; local http is allowed for loopback development"
    print(f"Main Computer hub server: http://{host}:{server.server_port}")
    print(
        f"Hub network: {config.hub_network} ({config.hub_network_kind}) "
        f"chain_id={config.chain_id} rpc={config.chain_rpc_url} runtime={server.hub_root}"
    )
    print(f"Hub security: high-security={config.hub_high_security} profile={HUB_SECURITY_PROFILE}; {scheme_note}")
    print(f"Hub admin/control site: http://{host}:{server.server_port}/admin")
    print(
        "Hub endpoints: GET /admin, GET /api/hub/v1/admin/bootstrap, GET /api/hub/status, "
        "GET /api/hub/payouts?node_id=..., POST /api/hub/v1/workers/register, "
        "POST /api/hub/v1/workers/heartbeat, POST /api/hub/v1/workers/poll, "
        "POST /api/hub/v1/workers/results, GET/POST /api/hub/v1/workers/claims, "
        "GET /api/hub/v1/workers/settlements, POST /api/hub/v1/workers/settlements/batches, POST /api/hub/v1/workers/settlements/proofs, POST /api/hub/v1/workers/settlements/chain-executions, "
        "POST /api/hub/sessions/start, POST /api/hub/sessions/chat, "
        "POST /api/hub/remote-overflow/safe-chat, POST /api/hub/v1/credits/multisession-keys/validate, POST /api/hub/payouts/claim"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nHub server stopped.")
    finally:
        server.server_close()


def serve_hub_worker(
    config: MainComputerConfig,
    chat_fn: Callable[[Sequence[ChatMessage]], ChatResponse],
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_HUB_WORKER_PORT,
    hub_url: str | None = None,
    public_endpoint: str | None = None,
    verbose: bool = True,
) -> None:
    server = HubWorkerHttpServer((host, port), config, chat_fn, verbose=verbose)
    endpoint = (public_endpoint or f"http://{host}:{server.server_port}").rstrip("/")
    if hub_url:
        report = register_worker_with_hub(
            hub_url=hub_url,
            node_id=config.hub_worker_node_id,
            endpoint=endpoint,
            model=config.model,
            credits_per_request=config.hub_credits_per_request,
            timeout_s=10.0,
            allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
        )
        if verbose:
            print(f"Registered hub worker {config.hub_worker_node_id}: {report.get('worker', {})}")
    print(f"Main Computer hub worker: {endpoint}")
    print(f"Worker security: high-security={config.hub_high_security} profile={HUB_SECURITY_PROFILE}")
    print(f"Worker endpoint: POST {HUB_WORKER_SESSION_START_PATH}, POST {HUB_WORKER_SESSION_CHAT_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nHub worker stopped.")
    finally:
        server.server_close()
