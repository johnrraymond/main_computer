from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from main_computer.multisession_key_signing import (
    build_personal_sign_blob as _build_personal_sign_blob,
    normalize_chain_id,
    private_key_to_address,
)


DEFAULT_HUB_URL = "http://127.0.0.1:8871"
DEFAULT_CLIENT_NODE_ID = "micro-agent-local"
DEFAULT_MODEL = "micro-agent-local"
DEFAULT_CAPABILITY = "chat.completions"
DEFAULT_APP_URL = ""
DEFAULT_CONTROL_APP_URL = "http://127.0.0.1:8765"
LEGACY_STANDALONE_WORKER_URL = "http://127.0.0.1:8771"
LOCAL_APP_FALLBACK_URLS = (
    DEFAULT_CONTROL_APP_URL,
    "http://127.0.0.1:28865",
    "http://127.0.0.1:38865",
    "http://127.0.0.1:18765",
    LEGACY_STANDALONE_WORKER_URL,
)
DEFAULT_WORKER_MODEL = "gemma4:26b"
DEFAULT_WORKER_CREDITS_PER_TOKEN = "0.001"
DEFAULT_WORKER_TARGET_TOKENS = 1024
DEFAULT_WORKER_WORK_NOW_SECONDS = 60 * 60
CREDIT_WEI_PER_CREDIT = 10**18
LOCAL_DEV_CHAIN_ID = 42424242
LOCAL_DEV_FUNDING_CONTRACT_ADDRESS = "0x0000000000000000000000000000000000000001"


class PrivateKeyResolution:
    def __init__(self, *, private_key: str, wallet_address: str, source: str, path: str = "") -> None:
        self.private_key = private_key
        self.wallet_address = wallet_address
        self.source = source
        self.path = path



def die(message: str, *, code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: float = 15.0) -> tuple[int, dict[str, Any]]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":"), sort_keys=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-supplied local Hub URL.
            raw = response.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return int(response.status), {}
            parsed = json.loads(raw)
            return int(response.status), parsed if isinstance(parsed, dict) else {"value": parsed}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"ok": False, "error": raw or str(exc)}
        return int(exc.code), parsed if isinstance(parsed, dict) else {"value": parsed}
    except URLError as exc:
        raise ConnectionError(f"could not reach {url}: {exc}") from exc


def normalize_market_ring(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "ring-3"
    match = re.search(r"\bring\s*[-:]?\s*(\d+)\b", text)
    if match:
        return f"ring-{int(match.group(1))}"
    match = re.search(r"\b(\d+)\b", text)
    if match:
        return f"ring-{int(match.group(1))}"
    if text.startswith("ring-"):
        return text
    return text.replace(" ", "-")


def _hub_id_from_status(hub_status: dict[str, Any]) -> str:
    raw = hub_status.get("hub_id")
    if isinstance(raw, dict):
        return str(raw.get("hub_id") or raw.get("id") or raw.get("display_name") or "").strip()
    serving = hub_status.get("serving_hub")
    if isinstance(serving, dict) and serving.get("hub_id"):
        return str(serving.get("hub_id") or "").strip()
    hub = hub_status.get("hub")
    if isinstance(hub, dict) and hub.get("hub_id"):
        return str(hub.get("hub_id") or "").strip()
    return str(raw or "").strip()


def extract_hub_status_summary(hub_status: dict[str, Any]) -> dict[str, str]:
    network = hub_status.get("network") if isinstance(hub_status.get("network"), dict) else {}
    hub_id = _hub_id_from_status(hub_status)
    serving = hub_status.get("serving_hub")
    if isinstance(serving, dict):
        serving_hub = str(serving.get("hub_id") or hub_id or "").strip()
    else:
        serving_hub = str(serving or hub_id or "").strip()
    backend = (
        hub_status.get("backend")
        or (hub_status.get("storage") or {}).get("backend")
        if isinstance(hub_status.get("storage"), dict)
        else hub_status.get("backend")
    )
    return {
        "ok": str(bool(hub_status.get("ok", True))),
        "hub_id": hub_id,
        "serving_hub": serving_hub,
        "network_key": str(network.get("network_key") or network.get("network") or hub_status.get("network_key") or "").strip(),
        "chain_id": normalize_chain_id(network.get("chain_id") or hub_status.get("chain_id") or ""),
        "backend": str(backend or "").strip(),
    }


def _capabilities_from_arg(value: Any) -> list[str]:
    raw = str(value or DEFAULT_CAPABILITY).strip()
    pieces = [piece.strip() for piece in re.split(r"[, ]+", raw) if piece.strip()]
    return pieces or [DEFAULT_CAPABILITY]


def credit_text_to_wei(value: Any) -> int:
    raw = str(value if value is not None else "0").strip() or "0"
    try:
        parsed = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"bad credit amount {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"credit amount must be non-negative: {value!r}")
    return int(parsed * CREDIT_WEI_PER_CREDIT)


def _whole_credits_for_payload(value: Any) -> int:
    try:
        parsed = Decimal(str(value if value is not None else "1"))
    except InvalidOperation:
        return 1
    if parsed <= 0:
        return 1
    # The Hub's live-session request body normalizes max_credits as an integer.
    return max(1, int(parsed))



def credit_wei_to_decimal_text(value: int | str) -> str:
    amount = int(value or 0)
    whole, remainder = divmod(amount, CREDIT_WEI_PER_CREDIT)
    if remainder == 0:
        return str(whole)
    fraction = str(remainder).rjust(18, "0").rstrip("0")
    return f"{whole}.{fraction}"


def _ring_number_from_arg(value: Any) -> str:
    ring = normalize_market_ring(value)
    match = re.search(r"(\d+)$", ring)
    return match.group(1) if match else "3"


def _network_key_from_status(hub_status: dict[str, Any]) -> str:
    summary = extract_hub_status_summary(hub_status or {})
    network = str(summary.get("network_key") or "dev").strip().lower()
    return network if network in {"mainnet", "testnet", "test", "dev"} else "dev"


def _worker_node_id_for_wallet(wallet_address: str) -> str:
    suffix = re.sub(r"[^0-9a-fA-F]", "", str(wallet_address or ""))[-12:].lower()
    return f"micro-agent-local-worker-{suffix or secrets.token_hex(6)}"


def _worker_price_fields(*, credits_per_token: str, target_tokens: int) -> dict[str, str | int]:
    token_wei = credit_text_to_wei(credits_per_token)
    request_wei = token_wei * max(1, int(target_tokens or DEFAULT_WORKER_TARGET_TOKENS))
    request_text = credit_wei_to_decimal_text(request_wei)
    return {
        "credits_per_token": credits_per_token,
        "credits_per_token_wei": str(token_wei),
        "target_output_tokens": max(1, int(target_tokens or DEFAULT_WORKER_TARGET_TOKENS)),
        "estimated_credits_per_request": request_text,
        "estimated_credits_per_request_wei": str(request_wei),
        "credits_per_request": request_text,
        "credits_per_request_wei": str(request_wei),
        "unit": "compute_credit",
    }


def build_local_worker_registration_payload(
    args: Any,
    *,
    wallet_address: str,
    app_url: str,
) -> dict[str, Any]:
    model = str(getattr(args, "worker_model", "") or DEFAULT_WORKER_MODEL).strip() or DEFAULT_WORKER_MODEL
    credits_per_token = str(getattr(args, "worker_credits_per_token", "") or DEFAULT_WORKER_CREDITS_PER_TOKEN).strip()
    target_tokens = int(getattr(args, "worker_target_tokens", DEFAULT_WORKER_TARGET_TOKENS) or DEFAULT_WORKER_TARGET_TOKENS)
    price = _worker_price_fields(credits_per_token=credits_per_token, target_tokens=target_tokens)
    capabilities = _capabilities_from_arg(getattr(args, "capability", DEFAULT_CAPABILITY))
    availability_mode = str(getattr(args, "worker_availability_mode", "") or "ai_idle").strip() or "ai_idle"
    if availability_mode not in {"ai_idle", "totally_idle"}:
        availability_mode = "ai_idle"
    availability = {
        "accept_paid_jobs": True,
        "availability_mode": availability_mode,
        "only_when_idle": availability_mode == "totally_idle",
        "idle_source": "windows_user_activity_v1" if availability_mode == "totally_idle" else "local_ai_capacity_v1",
        "ai_idle_required": availability_mode == "ai_idle",
    }
    execution = {"mode": "exp_live_session_direct_v1", "max_concurrency": 1}
    return {
        "node_id": _worker_node_id_for_wallet(wallet_address),
        "endpoint": worker_endpoint_from_args(args),
        "model": model,
        "models": [model],
        "credits_per_token": price["credits_per_token"],
        "credits_per_token_wei": price["credits_per_token_wei"],
        "estimated_credits_per_request": price["estimated_credits_per_request"],
        "estimated_credits_per_request_wei": price["estimated_credits_per_request_wei"],
        "credits_per_request": price["credits_per_request"],
        "credits_per_request_wei": price["credits_per_request_wei"],
        "target_output_tokens": price["target_output_tokens"],
        "max_concurrency": 1,
        "queue_depth": 0,
        "active_requests": 0,
        "pricing": {"pricing_type": "approx_per_token_v0", **price},
        "execution": execution,
        "availability": availability,
        "capabilities": {
            "capabilities": capabilities,
            "pricing": {"pricing_type": "approx_per_token_v0", **price},
            "execution": execution,
            "availability": availability,
            "target_output_tokens": price["target_output_tokens"],
            "phase12_worker_seller_offer_ui": True,
            "credit_wallet": wallet_address,
            "wallet_address": wallet_address,
        },
    }


def build_local_worker_settings(
    args: Any,
    *,
    hub_url: str,
    hub_status: dict[str, Any],
    wallet_address: str,
    app_url: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = dict(existing or {})
    network = _network_key_from_status(hub_status)
    ring = _ring_number_from_arg(getattr(args, "ring", "3"))
    worker = build_local_worker_registration_payload(args, wallet_address=wallet_address, app_url=app_url)
    availability_mode = str(getattr(args, "worker_availability_mode", "") or "ai_idle")
    settings.update(
        {
            "selectedNetwork": network,
            "workerAutoConnectNetwork": network,
            "workerRequestedRing": ring,
            "workerConnectionStatus": "connected",
            "workerConnectedHubUrl": hub_url,
            "workerConnectionError": "",
            "sellerEnabled": True,
            "rentalEnabled": True,
            "sellerAvailabilityMode": availability_mode,
            "sellerOnlyWhenIdle": availability_mode == "totally_idle",
            "rentalOnlyWhenIdle": availability_mode == "totally_idle",
            "registrationHubUrl": hub_url,
            "nodeId": worker["node_id"],
            "endpoint": worker["endpoint"],
            "models": ",".join(worker["models"]),
            "sellerTargetTokens": int(worker["target_output_tokens"]),
            "capability": worker["capabilities"]["capabilities"][0],
            "sellerCreditsPerToken": worker["credits_per_token"],
            "maxConcurrency": 1,
            "executionMode": worker["execution"]["mode"],
        }
    )
    return settings


def build_local_worker_work_now_payload(
    args: Any,
    *,
    hub_url: str,
    hub_status: dict[str, Any],
    wallet_address: str,
    app_url: str,
    active_multisession_key_id: str = "",
    duration_seconds: int | None = None,
) -> dict[str, Any]:
    network = _network_key_from_status(hub_status)
    ring = _ring_number_from_arg(getattr(args, "ring", "3"))
    chain_id = normalize_chain_id(_chain_id_from_status(hub_status))
    payload = {
        "action": "work-now",
        "duration_seconds": int(duration_seconds or getattr(args, "auto_worker_seconds", DEFAULT_WORKER_WORK_NOW_SECONDS) or DEFAULT_WORKER_WORK_NOW_SECONDS),
        "active_jobs": 0,
        "hub_url": hub_url,
        "network": network,
        "chain_id": chain_id,
        "requested_ring": ring,
        "wallet_address": wallet_address,
        "credit_wallet": wallet_address,
        "worker": build_local_worker_registration_payload(args, wallet_address=wallet_address, app_url=app_url),
    }
    if active_multisession_key_id:
        payload["active_multisession_key_id"] = active_multisession_key_id
        payload["multisession_key_id"] = active_multisession_key_id
    return payload


def build_work_payload(args: Any, authorization: dict[str, Any], hub_status: dict[str, Any]) -> dict[str, Any]:
    ring = normalize_market_ring(getattr(args, "ring", "3"))
    capability_values = _capabilities_from_arg(getattr(args, "capability", None))
    prompt = str(getattr(args, "prompt", "") or "")
    max_credits = str(getattr(args, "max_credits", "2") or "2")
    summary = extract_hub_status_summary(hub_status or {})
    chain_id = summary.get("chain_id") or normalize_chain_id((hub_status.get("network") or {}).get("chain_id") if isinstance(hub_status.get("network"), dict) else "")
    model = str(getattr(args, "model", DEFAULT_MODEL) or DEFAULT_MODEL)
    client_node_id = str(getattr(args, "client_node_id", DEFAULT_CLIENT_NODE_ID) or DEFAULT_CLIENT_NODE_ID)

    metadata: dict[str, Any] = {
        "micro_agent": "local-canvas-v1",
        "requested_model": model,
        "execution_mode": "exp-live-session-worker-v1",
        "worker_connection_mode": "stable-websocket-live-session",
    }
    if chain_id:
        metadata["hub_chain_id"] = chain_id
        metadata["chain_id"] = chain_id
    if summary.get("hub_id"):
        metadata["hub_id"] = summary["hub_id"]

    clean_authorization = dict(authorization or {})
    if clean_authorization:
        authz = {
            "kind": "multisession_key",
            "wallet_address": clean_authorization.get("wallet_address"),
            "multisession_key_id": clean_authorization.get("multisession_key_id") or clean_authorization.get("key_id"),
            "key_id": clean_authorization.get("key_id") or clean_authorization.get("multisession_key_id"),
            "chain_id": normalize_chain_id(clean_authorization.get("chain_id") or chain_id),
            "max_authorized_credit_wei": str(
                clean_authorization.get("max_authorized_credit_wei")
                or credit_text_to_wei(max_credits)
            ),
        }
        metadata["auth_mode"] = "multisession-wallet"
        metadata["multisession_key_authorized"] = True
        metadata["multisession_key_id"] = authz["multisession_key_id"]
        metadata["payment_authorization"] = authz
        metadata["multisession_authorization"] = authz
    else:
        authz = {}

    payload: dict[str, Any] = {
        "client_node_id": client_node_id,
        "model": model,
        "ring": ring,
        "partition": ring,
        "capabilities": capability_values,
        "required_capabilities": capability_values,
        "max_price": {"amount": max_credits, "unit": "compute_credit"},
        "max_credits": _whole_credits_for_payload(max_credits),
        "deadline_seconds": 0.0,
        "accept_timeout_seconds": float(getattr(args, "accept_timeout", 10.0) or 10.0),
        "idempotency_key": f"micro_req_{secrets.token_hex(16)}",
        "input": {
            "kind": capability_values[0],
            "prompt": prompt,
            "messages": [{"role": "user", "content": prompt}],
        },
        "messages": [{"role": "user", "content": prompt}],
        "metadata": metadata,
    }
    if authz:
        payload["payment_authorization"] = authz
        payload["multisession_authorization"] = authz
    return payload


def build_multisession_key_message(*, wallet_address: str, chain_id: Any, hub_url: str, lifetime_minutes: int = 10) -> dict[str, Any]:
    now = utc_now()
    expires = now + timedelta(minutes=max(1, int(lifetime_minutes or 10)))
    base_url = str(hub_url or DEFAULT_HUB_URL).strip().rstrip("/")
    return {
        "purpose": "request_multi_session_key",
        "request_id": f"msk_req_{secrets.token_hex(16)}",
        "wallet_address": wallet_address,
        "chain_id": normalize_chain_id(chain_id),
        "user_slug": "usr_" + secrets.token_hex(16),
        "origin": f"micro-agent-canvas:{base_url}",
        "issued_at": now.isoformat(),
        "expires_at": expires.isoformat(),
    }


def build_personal_sign_blob(*, message: dict[str, Any], private_key: Any, wallet_address: Any | None = None, chain_id: Any | None = None) -> dict[str, Any]:
    return _build_personal_sign_blob(
        message=message,
        private_key=private_key,
        wallet_address=wallet_address,
        chain_id=chain_id,
    )


def _chain_id_from_status(hub_status: dict[str, Any]) -> str:
    summary = extract_hub_status_summary(hub_status or {})
    return summary.get("chain_id") or "42424242"


def _is_local_dev_chain(chain_id: Any) -> bool:
    try:
        return int(normalize_chain_id(chain_id) or "0") == LOCAL_DEV_CHAIN_ID
    except (TypeError, ValueError):
        return False


def _looks_like_evm_address(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) == 42 and text.lower().startswith("0x") and all(ch in "0123456789abcdefABCDEF" for ch in text[2:])


def _nested_find_contract_address(value: Any) -> str:
    if isinstance(value, dict):
        for key in (
            "contract_address",
            "address",
            "hub_credit_bridge_escrow_address",
            "hubCreditBridgeEscrowAddress",
        ):
            candidate = str(value.get(key) or "").strip()
            if _looks_like_evm_address(candidate):
                return candidate
        for nested in value.values():
            candidate = _nested_find_contract_address(nested)
            if candidate:
                return candidate
    if isinstance(value, list):
        for item in value:
            candidate = _nested_find_contract_address(item)
            if candidate:
                return candidate
    return ""


def _deployment_contract_address(*, root: Path, network_key: str) -> str:
    network = str(network_key or "dev").strip() or "dev"
    candidates = [
        root / "runtime" / "deployments" / network / "latest.json",
        root / "runtime" / "deployments" / "dev" / "latest.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        contracts = data.get("contracts") if isinstance(data, dict) else {}
        candidate = _nested_find_contract_address(contracts)
        if candidate:
            return candidate
    return ""


def resolve_wallet_funding_contract_address(
    *,
    hub_status: dict[str, Any],
    chain_id: Any,
    settings: dict[str, Any] | None = None,
    root: Path | None = None,
) -> str:
    settings = settings or {}
    root = root or Path.cwd()
    for candidate in (
        settings.get("contract_address"),
        settings.get("hub_credit_bridge_escrow_address"),
        os.environ.get("MAIN_COMPUTER_HUB_CREDIT_BRIDGE_ESCROW_ADDRESS"),
        os.environ.get("MAIN_COMPUTER_DEV_FUNDING_CONTRACT_ADDRESS"),
        _nested_find_contract_address(hub_status),
        _deployment_contract_address(
            root=root,
            network_key=extract_hub_status_summary(hub_status or {}).get("network_key") or "dev",
        ),
    ):
        if _looks_like_evm_address(candidate):
            return str(candidate).strip()
    if _is_local_dev_chain(chain_id):
        return LOCAL_DEV_FUNDING_CONTRACT_ADDRESS
    return ""


def build_wallet_funding_import_payload(
    *,
    wallet_address: str,
    chain_id: Any,
    max_credit_wei: str,
    hub_status: dict[str, Any],
    settings: dict[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    normalized_chain_id = int(normalize_chain_id(chain_id) or "0")
    contract_address = resolve_wallet_funding_contract_address(
        hub_status=hub_status,
        chain_id=normalized_chain_id,
        settings=settings or {},
        root=root or Path.cwd(),
    )
    if not contract_address:
        raise RuntimeError(
            "wallet funding needs a bridge escrow contract address. Set "
            "MAIN_COMPUTER_HUB_CREDIT_BRIDGE_ESCROW_ADDRESS or run the local dev chain/deployment bootstrap."
        )
    credit_wei = str(max_credit_wei or "").strip()
    seed = json.dumps(
        {
            "kind": "micro_agent_canvas_dev_wallet_funding_v1",
            "wallet_address": str(wallet_address or "").strip().lower(),
            "chain_id": str(normalized_chain_id),
            "contract_address": contract_address.lower(),
            "credits_granted_wei": credit_wei,
        },
        sort_keys=True,
    ).encode("utf-8")
    tx_hash = "0x" + hashlib.sha256(seed).hexdigest()
    return {
        "wallet_address": wallet_address,
        "chain_id": normalized_chain_id,
        "contract_address": contract_address,
        "tx_hash": tx_hash,
        "log_index": 0,
        "block_number": 1,
        "payment_asset": "native",
        "payment_amount_base_units": max(1, int(credit_wei or "0")),
        "credits_granted_wei": credit_wei,
        "idempotency_key": f"micro-agent-canvas-funding-{hashlib.sha256(seed).hexdigest()[:24]}",
        "memo": f"micro agent canvas local dev wallet funding for {wallet_address}",
        "metadata": {
            "source": "micro_agent_canvas",
            "synthetic_local_dev_receipt": _is_local_dev_chain(normalized_chain_id),
        },
    }


def request_fresh_multisession_authorization(
    *,
    args: Any,
    hub_url: str,
    hub_status: dict[str, Any],
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or {}
    resolution = resolve_requester_private_key(args, hub_status=hub_status, settings=settings, root=Path.cwd())
    chain_id = _chain_id_from_status(hub_status)
    max_credit_wei = str(credit_text_to_wei(getattr(args, "max_credits", "2") or "2"))
    message = build_multisession_key_message(
        wallet_address=resolution.wallet_address,
        chain_id=chain_id,
        hub_url=hub_url,
        lifetime_minutes=int(getattr(args, "msk_lifetime_minutes", 10) or 10),
    )
    signed = build_personal_sign_blob(
        message=message,
        private_key=resolution.private_key,
        wallet_address=resolution.wallet_address,
        chain_id=chain_id,
    )

    base = str(hub_url or DEFAULT_HUB_URL).rstrip("/")
    status, requested = http_json(
        "POST",
        f"{base}/api/hub/v1/credits/multisession-keys/request",
        {"signed_request": signed},
        timeout=15.0,
    )
    if status >= 400 or requested.get("ok") is False:
        raise RuntimeError(f"multi-session key request failed HTTP {status}: {requested}")

    auth = requested.get("multisession_authorization") if isinstance(requested.get("multisession_authorization"), dict) else {}
    key = requested.get("key") if isinstance(requested.get("key"), dict) else {}
    key_id = str(auth.get("multisession_key_id") or auth.get("key_id") or key.get("id") or "").strip()
    if not key_id:
        raise RuntimeError(f"multi-session key response did not include a key id: {requested}")

    authorization = {
        "kind": "multisession_key",
        "wallet_address": resolution.wallet_address,
        "multisession_key_id": key_id,
        "key_id": key_id,
        "chain_id": normalize_chain_id(auth.get("chain_id") or chain_id),
        "max_authorized_credit_wei": max_credit_wei,
    }

    funding_payload = build_wallet_funding_import_payload(
        wallet_address=resolution.wallet_address,
        chain_id=chain_id,
        max_credit_wei=max_credit_wei,
        hub_status=hub_status,
        settings=settings,
        root=Path.cwd(),
    )
    fund_status, funding = http_json(
        "POST",
        f"{base}/api/hub/v1/credits/wallet-funding/import",
        funding_payload,
        timeout=15.0,
    )
    if fund_status >= 400 or funding.get("ok") is False:
        detail = str(funding.get("error") or funding)
        if "contract_address" in detail:
            detail += (
                " (the Hub wallet-funding import endpoint expects a normalized bridge receipt; "
                "micro_agent_canvas supplies a deterministic local-dev receipt when the dev chain id is 42424242)"
            )
        raise RuntimeError(f"wallet funding import failed HTTP {fund_status}: {detail}")
    account = funding.get("account") if isinstance(funding.get("account"), dict) else {}
    print(
        "[funding] dev wallet funding imported/idempotent="
        f"{bool(funding.get('idempotent', False))} available={account.get('available_credit_wei', '')}"
    )

    validate_payload = {
        "wallet_address": resolution.wallet_address,
        "chain_id": normalize_chain_id(chain_id),
        "required_credit_wei": max_credit_wei,
        "multisession_authorization": authorization,
        "payment_authorization": authorization,
    }
    validate_status, validated = http_json(
        "POST",
        f"{base}/api/hub/v1/credits/multisession-keys/validate",
        validate_payload,
        timeout=15.0,
    )
    if validate_status >= 400 or validated.get("ok") is False or validated.get("valid") is False:
        raise RuntimeError(f"multi-session key validation failed HTTP {validate_status}: {validated}")

    print(
        f"[auth] wallet={resolution.wallet_address[:10]}…{resolution.wallet_address[-6:]} "
        f"msk={key_id[:10]}… chain_id={authorization['chain_id']}"
    )
    return authorization


def _read_private_key_file(path: Path) -> str:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise ValueError(f"private key file is empty: {path}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict):
        for key in ("private_key", "privateKey", "key"):
            if data.get(key):
                return str(data[key]).strip()
    raise ValueError(f"private key file does not contain private_key: {path}")


def _deployment_wallet_candidates(root: Path, chain_id: str) -> list[Path]:
    candidates: list[Path] = []
    deployment_dir = root / "runtime" / "deployments" / "dev"
    latest = deployment_dir / "latest.json"
    if latest.exists():
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
            smoke = data.get("smoke_client") if isinstance(data.get("smoke_client"), dict) else {}
            wallet_path = smoke.get("wallet_path")
            if wallet_path:
                candidates.append(root / str(wallet_path))
            chain = data.get("chain") if isinstance(data.get("chain"), dict) else {}
            found_chain = normalize_chain_id(chain.get("chain_id") or chain_id)
            if found_chain:
                candidates.append(deployment_dir / f"smoke-client-wallet-{found_chain}.json")
        except Exception:
            pass
    if chain_id:
        candidates.append(deployment_dir / f"smoke-client-wallet-{normalize_chain_id(chain_id)}.json")
    candidates.extend(sorted(deployment_dir.glob("smoke-client-wallet-*.json")))
    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def resolve_requester_private_key(
    args: Any,
    *,
    hub_status: dict[str, Any],
    settings: dict[str, Any] | None = None,
    root: Path | None = None,
) -> PrivateKeyResolution:
    root = Path(root or Path.cwd())
    explicit_key = str(getattr(args, "private_key", "") or "").strip()
    explicit_file = str(getattr(args, "private_key_file", "") or "").strip()
    if explicit_key:
        return PrivateKeyResolution(
            private_key=explicit_key,
            wallet_address=private_key_to_address(explicit_key),
            source="--private-key",
        )
    if explicit_file:
        path = Path(explicit_file).expanduser()
        key = _read_private_key_file(path)
        return PrivateKeyResolution(
            private_key=key,
            wallet_address=private_key_to_address(key),
            source="--private-key-file",
            path=str(path),
        )

    for env_name in (
        "MICRO_AGENT_PRIVATE_KEY",
        "MAIN_COMPUTER_MICRO_AGENT_PRIVATE_KEY",
        "MAIN_COMPUTER_SMOKE_CLIENT_PRIVATE_KEY",
        "MAIN_COMPUTER_PAID_REQUESTER_PRIVATE_KEY",
        "MAIN_COMPUTER_REQUESTER_0_PRIVATE_KEY",
    ):
        value = os.environ.get(env_name, "").strip()
        if value:
            return PrivateKeyResolution(
                private_key=value,
                wallet_address=private_key_to_address(value),
                source=env_name,
            )

    chain_id = _chain_id_from_status(hub_status)
    for candidate in _deployment_wallet_candidates(root, chain_id):
        if not candidate.exists():
            continue
        try:
            key = _read_private_key_file(candidate)
            return PrivateKeyResolution(
                private_key=key,
                wallet_address=private_key_to_address(key),
                source="deployment smoke-client wallet",
                path=str(candidate),
            )
        except Exception:
            continue

    settings = settings or {}
    for key_name in ("private_key", "requester_private_key", "smoke_client_private_key"):
        if settings.get(key_name):
            key = str(settings[key_name]).strip()
            return PrivateKeyResolution(
                private_key=key,
                wallet_address=private_key_to_address(key),
                source=f"settings.{key_name}",
            )

    raise RuntimeError(
        "No requester private key found. Use --private-key, --private-key-file, "
        "or generate runtime/deployments/dev/smoke-client-wallet-<chain>.json."
    )


def _nested_get(payload: Any, path: tuple[Any, ...]) -> Any:
    current = payload
    for key in path:
        if isinstance(key, int):
            if not isinstance(current, list) or key >= len(current):
                return None
            current = current[key]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
    return current


def _text_from_candidate(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, dict):
        for key in ("content", "text", "message", "response_summary", "summary", "value"):
            found = _text_from_candidate(value.get(key))
            if found:
                return found
        choices = value.get("choices")
        if isinstance(choices, list) and choices:
            for choice in choices:
                found = _text_from_candidate(choice)
                if found:
                    return found
    if isinstance(value, list):
        for item in value:
            found = _text_from_candidate(item)
            if found:
                return found
    return ""


def extract_simple_text_result(payload: dict[str, Any]) -> str:
    """Return user-visible text from both legacy and live-session final payloads."""

    if not isinstance(payload, dict):
        return ""

    direct_paths = (
        ("request", "response", "content"),
        ("request", "response", "message", "content"),
        ("request", "response_summary"),
        ("response", "content"),
        ("response", "message", "content"),
        ("result", "content"),
        ("result", "response", "content"),
        ("output", "content"),
        ("content",),
        ("text",),
    )
    for path in direct_paths:
        found = _text_from_candidate(_nested_get(payload, path))
        if found:
            return found

    stream = payload.get("stream") if isinstance(payload.get("stream"), dict) else {}
    events = stream.get("events") if isinstance(stream.get("events"), list) else []
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if str(event.get("type") or "") == "result" or str(event.get("status") or "") in {"succeeded", "completed"}:
            for key in ("content", "response", "result", "message"):
                found = _text_from_candidate(event.get(key))
                if found:
                    return found
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        found = _text_from_candidate(event.get("content_so_far"))
        if found:
            return found

    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    found = _text_from_candidate(request.get("response"))
    if found:
        return found

    return ""


def _with_sse_query(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["format"] = ["sse"]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _print_delta(event: dict[str, Any], state: dict[str, Any]) -> None:
    delta = event.get("delta")
    if not isinstance(delta, str) or not delta:
        return
    if not state.get("started"):
        print("[stream] ", end="", flush=True)
        state["started"] = True
    print(delta, end="", flush=True)
    state["printed_any_delta"] = True


def _print_status(status: Any, state: dict[str, Any]) -> None:
    text = str(status or "").strip()
    if not text or state.get("last_status") == text:
        return
    if state.get("printed_any_delta"):
        print()
        state["printed_any_delta"] = False
    print(f"[session] status={text}")
    state["last_status"] = text


def _parse_sse_stream(url: str, timeout: float, state: dict[str, Any]) -> dict[str, Any] | None:
    request = Request(url, headers={"Accept": "text/event-stream"}, method="GET")
    last_event = "message"
    data_lines: list[str] = []
    terminal_event: dict[str, Any] | None = None
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-supplied local Hub URL.
        while True:
            raw_line = response.readline()
            if raw_line == b"":
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if data_lines:
                    data_text = "\n".join(data_lines)
                    data_lines = []
                    try:
                        event_payload = json.loads(data_text)
                    except json.JSONDecodeError:
                        last_event = "message"
                        continue
                    if isinstance(event_payload, dict):
                        event_payload.setdefault("type", last_event)
                        _print_status(event_payload.get("status"), state)
                        _print_delta(event_payload, state)
                        event_type = str(event_payload.get("type") or last_event)
                        status = str(event_payload.get("status") or "")
                        if event_type in {"result", "failed", "cancelled"} or status in {"succeeded", "completed", "failed", "cancelled"}:
                            terminal_event = event_payload
                            break
                last_event = "message"
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                last_event = value or "message"
            elif field == "data":
                data_lines.append(value)
    if state.get("started"):
        print()
        state["started"] = False
    return terminal_event


def follow_work_session(continuation_url: str, *, poll_timeout: float = 300.0, poll_interval: float = 1.0) -> dict[str, Any]:
    print(f"[session] {continuation_url}")
    state: dict[str, Any] = {"last_status": None, "printed_any_delta": False, "started": False}
    deadline = time.monotonic() + max(1.0, poll_timeout)
    final_payload: dict[str, Any] = {}

    status, snapshot = http_json("GET", continuation_url, timeout=15.0)
    if status >= 400:
        return snapshot
    final_payload = snapshot
    _print_status(snapshot.get("status"), state)

    stream = snapshot.get("stream") if isinstance(snapshot.get("stream"), dict) else {}
    realtime = stream.get("realtime") if isinstance(stream.get("realtime"), dict) else {}
    sse_url = str(realtime.get("url") or "").strip() or _with_sse_query(continuation_url)
    try:
        _parse_sse_stream(sse_url, timeout=max(1.0, min(poll_timeout, 3600.0)), state=state)
    except Exception as exc:
        print(f"[session] realtime stream unavailable, falling back to polling: {exc}", file=sys.stderr)

    while time.monotonic() < deadline:
        status, snapshot = http_json("GET", continuation_url, timeout=15.0)
        final_payload = snapshot
        _print_status(snapshot.get("status"), state)
        if str(snapshot.get("status") or "") in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(max(0.1, poll_interval))
    return final_payload



def _normalize_base_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    return text.rstrip("/")


def _append_unique_url(candidates: list[str], value: Any) -> None:
    url = _normalize_base_url(value)
    if url and url not in candidates:
        candidates.append(url)


def _port_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        port = int(text)
    except (TypeError, ValueError):
        return ""
    if port <= 0 or port > 65535:
        return ""
    return f"http://127.0.0.1:{port}"


def _collect_urls_from_start_session(root: Path) -> list[str]:
    candidates: list[str] = []
    session_path = root / "runtime" / "start_stop" / "start-session.json"
    if not session_path.exists():
        return candidates
    try:
        session = json.loads(session_path.read_text(encoding="utf-8"))
    except Exception:
        return candidates
    if not isinstance(session, dict):
        return candidates

    env = session.get("environment") if isinstance(session.get("environment"), dict) else {}
    for key in (
        "MAIN_COMPUTER_APP_URL",
        "MAIN_COMPUTER_CONTROL_URL",
        "MAIN_COMPUTER_VIEWPORT_URL",
    ):
        _append_unique_url(candidates, env.get(key))
    for key in (
        "MAIN_COMPUTER_CONTROL_PORT",
        "MAIN_COMPUTER_VIEWPORT_PORT",
        "MAIN_COMPUTER_APP_PORT",
    ):
        _append_unique_url(candidates, _port_url(env.get(key)))

    for key in ("app_url", "control_url", "viewport_url", "url"):
        _append_unique_url(candidates, session.get(key))

    # Some launcher snapshots record argv rather than a normalized environment map.
    for key in ("argv", "args", "command", "command_line"):
        value = session.get(key)
        parts: list[str] = []
        if isinstance(value, str):
            parts = value.split()
        elif isinstance(value, list):
            parts = [str(item) for item in value]
        for index, part in enumerate(parts):
            if part in {"--port", "--control-port", "--viewport-port"} and index + 1 < len(parts):
                _append_unique_url(candidates, _port_url(parts[index + 1]))
            elif part.startswith("--port=") or part.startswith("--control-port=") or part.startswith("--viewport-port="):
                _append_unique_url(candidates, _port_url(part.split("=", 1)[1]))
    return candidates


def local_worker_app_url_candidates(args: Any, *, root: Path | None = None) -> list[str]:
    """Return local Main Computer app/control URLs to probe for the Worker page API.

    The Worker page API is served by the local viewport/control app, normally port
    8765.  Port 8771 is only the legacy standalone hub-worker port, so it is kept
    as a last fallback instead of being the default.
    """

    root = root or Path.cwd()
    candidates: list[str] = []
    _append_unique_url(candidates, getattr(args, "app", ""))
    for key in (
        "MAIN_COMPUTER_APP_URL",
        "MAIN_COMPUTER_CONTROL_URL",
        "MAIN_COMPUTER_VIEWPORT_URL",
    ):
        _append_unique_url(candidates, os.environ.get(key))
    for key in (
        "MAIN_COMPUTER_CONTROL_PORT",
        "MAIN_COMPUTER_VIEWPORT_PORT",
        "MAIN_COMPUTER_APP_PORT",
    ):
        _append_unique_url(candidates, _port_url(os.environ.get(key)))
    for url in _collect_urls_from_start_session(root):
        _append_unique_url(candidates, url)
    for url in LOCAL_APP_FALLBACK_URLS:
        _append_unique_url(candidates, url)
    return candidates


def resolve_local_worker_app_url(args: Any, *, root: Path | None = None, timeout: float = 2.0) -> str:
    tried: list[str] = []
    for base in local_worker_app_url_candidates(args, root=root):
        status, payload = http_json("GET", f"{base}/api/applications/worker/runtime-status", timeout=timeout)
        if status < 400 and payload.get("ok") is not False:
            return base
        detail = str(payload.get("error") or payload.get("message") or f"HTTP {status}")
        tried.append(f"{base} ({detail})")
    tried_text = "; ".join(tried) if tried else "no candidates"
    raise RuntimeError(
        "could not reach the local Main Computer Worker page API. "
        "The app/control server is usually http://127.0.0.1:8765; "
        "http://127.0.0.1:8771 is the legacy standalone hub-worker port. "
        f"Tried: {tried_text}"
    )


def _app_url_from_args(args: Any) -> str:
    return _normalize_base_url(getattr(args, "app", "") or os.environ.get("MAIN_COMPUTER_APP_URL") or DEFAULT_APP_URL)


def worker_endpoint_from_args(args: Any) -> str:
    return _normalize_base_url(
        getattr(args, "worker_endpoint", "")
        or os.environ.get("MAIN_COMPUTER_MICRO_AGENT_WORKER_ENDPOINT")
        or LEGACY_STANDALONE_WORKER_URL
    )


def _runtime_indicates_live_worker(payload: dict[str, Any]) -> bool:
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else payload
    if not isinstance(runtime, dict):
        return False
    phase = str(runtime.get("phase") or "").lower()
    state = str(runtime.get("state") or "").upper()
    allowed = bool(runtime.get("allowed_to_accept") or runtime.get("allowedToAccept"))
    hub_status = str(runtime.get("hub_status") or runtime.get("hubAvailability") or "").lower()
    return phase == "accepting" and (allowed or state in {"CONNECTED", "ACTIVE"} or hub_status in {"available", "busy"})


def _key_id_from_worker_key_record(record: dict[str, Any]) -> str:
    return str(
        record.get("id")
        or record.get("key_id")
        or record.get("multisession_key_id")
        or ""
    ).strip()


def _worker_key_record_is_active(record: dict[str, Any]) -> bool:
    return str(record.get("status") or "").strip().lower() == "active"


def _load_or_request_app_multisession_key(
    *,
    args: Any,
    app_url: str,
    hub_url: str,
    hub_status: dict[str, Any],
    resolution: PrivateKeyResolution,
) -> str:
    """Ensure the app has a worker multi-session key and return its id when public.

    Worker key ids are bearer credentials. The Worker app intentionally redacts
    them from public load/request responses after they have been stored locally.
    That is still a usable state: /work-now can resolve the active saved key on
    the app/control server by wallet + Hub. Returning an empty string means
    "cached server-side key exists; do not put a key id in the client payload."
    """
    base = app_url.rstrip("/")
    chain_id = normalize_chain_id(_chain_id_from_status(hub_status))
    load_payload = {"wallet_address": resolution.wallet_address, "hub_url": hub_url}

    def active_key_id_or_server_side_marker(container: dict[str, Any]) -> tuple[bool, str]:
        active = container.get("active_key") if isinstance(container.get("active_key"), dict) else {}
        if not active and isinstance(container.get("key"), dict):
            active = container["key"]
        if not isinstance(active, dict) or not _worker_key_record_is_active(active):
            return False, ""
        key_id = _key_id_from_worker_key_record(active)
        return True, key_id

    status, loaded = http_json(
        "POST",
        f"{base}/api/applications/worker/multisession-keys/load",
        load_payload,
        timeout=15.0,
    )
    if status < 400 and loaded.get("ok") is not False:
        has_active_key, key_id = active_key_id_or_server_side_marker(loaded)
        if key_id:
            print(f"[worker] using saved worker multi-session key {key_id[:10]}…")
            return key_id
        if has_active_key:
            print("[worker] using saved server-side worker multi-session key.")
            return ""

    message = build_multisession_key_message(
        wallet_address=resolution.wallet_address,
        chain_id=chain_id,
        hub_url=hub_url,
        lifetime_minutes=int(getattr(args, "msk_lifetime_minutes", 10) or 10),
    )
    signed = build_personal_sign_blob(
        message=message,
        private_key=resolution.private_key,
        wallet_address=resolution.wallet_address,
        chain_id=chain_id,
    )
    request_payload = {
        "hub_url": hub_url,
        "signed_request": signed,
        "client_metadata": {
            "source": "micro-agent-canvas-auto-worker",
            "wallet_address": resolution.wallet_address,
            "chain_id": chain_id,
        },
    }
    status, requested = http_json(
        "POST",
        f"{base}/api/applications/worker/multisession-key/request",
        request_payload,
        timeout=20.0,
    )
    if status >= 400 or requested.get("ok") is False:
        detail = str(requested.get("error") or requested)
        if "active saved multi-session key" in detail:
            reload_status, reloaded = http_json(
                "POST",
                f"{base}/api/applications/worker/multisession-keys/load",
                load_payload,
                timeout=15.0,
            )
            if reload_status < 400:
                has_active_key, key_id = active_key_id_or_server_side_marker(reloaded)
                if key_id:
                    return key_id
                if has_active_key:
                    print("[worker] using saved server-side worker multi-session key.")
                    return ""
        raise RuntimeError(f"worker multi-session key request failed HTTP {status}: {requested}")

    for container_name in ("local_cache", ""):
        container = requested.get(container_name) if container_name else requested
        if isinstance(container, dict):
            key = container.get("key") if isinstance(container.get("key"), dict) else {}
            key_id = _key_id_from_worker_key_record(key)
            if key_id:
                print(f"[worker] issued local worker multi-session key {key_id[:10]}…")
                return key_id
            if _worker_key_record_is_active(key):
                print("[worker] saved server-side worker multi-session key.")
                return ""
    raise RuntimeError(f"worker multi-session key response did not include an active key record: {requested}")


def ensure_local_worker_available(
    *,
    args: Any,
    hub_url: str,
    hub_status: dict[str, Any],
) -> bool:
    if bool(getattr(args, "no_auto_worker", False)):
        return False
    if _network_key_from_status(hub_status) != "dev":
        print("[worker] automatic local worker setup is only enabled for the dev hub.", file=sys.stderr)
        return False

    base = resolve_local_worker_app_url(args, root=Path.cwd()).rstrip("/")
    status, runtime = http_json("GET", f"{base}/api/applications/worker/runtime-status", timeout=10.0)
    if status < 400 and _runtime_indicates_live_worker(runtime):
        print("[worker] local app already reports an accepting live worker.")
        return True

    print(f"[worker] no live worker matched; preparing local dev worker through {base}")
    resolution = resolve_requester_private_key(args, hub_status=hub_status, settings={}, root=Path.cwd())
    network = _network_key_from_status(hub_status)
    ring = _ring_number_from_arg(getattr(args, "ring", "3"))

    status, selected = http_json(
        "POST",
        f"{base}/api/applications/worker/network-session",
        {"network": network, "requested_ring": ring},
        timeout=15.0,
    )
    if status >= 400 or selected.get("ok") is False:
        raise RuntimeError(f"local worker network selection failed HTTP {status}: {selected}")

    settings: dict[str, Any] = {}
    status, loaded_settings = http_json("GET", f"{base}/api/applications/worker/settings", timeout=10.0)
    if status < 400 and isinstance(loaded_settings.get("settings"), dict):
        settings = dict(loaded_settings["settings"])
    settings = build_local_worker_settings(
        args,
        hub_url=hub_url,
        hub_status=hub_status,
        wallet_address=resolution.wallet_address,
        app_url=base,
        existing=settings,
    )
    status, saved_settings = http_json(
        "POST",
        f"{base}/api/applications/worker/settings",
        {"settings": settings},
        timeout=15.0,
    )
    if status >= 400 or saved_settings.get("ok") is False:
        raise RuntimeError(f"local worker settings save failed HTTP {status}: {saved_settings}")

    key_id = _load_or_request_app_multisession_key(
        args=args,
        app_url=base,
        hub_url=hub_url,
        hub_status=hub_status,
        resolution=resolution,
    )
    work_now_payload = build_local_worker_work_now_payload(
        args,
        hub_url=hub_url,
        hub_status=hub_status,
        wallet_address=resolution.wallet_address,
        app_url=base,
        active_multisession_key_id=key_id,
    )
    status, work_now = http_json(
        "POST",
        f"{base}/api/applications/worker/work-now",
        work_now_payload,
        timeout=30.0,
    )
    if status >= 400 or work_now.get("ok") is False:
        raise RuntimeError(f"local worker Work-now setup failed HTTP {status}: {work_now}")

    deadline = time.monotonic() + float(getattr(args, "auto_worker_timeout", 10.0) or 10.0)
    last_runtime: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, last_runtime = http_json("GET", f"{base}/api/applications/worker/runtime-status", timeout=10.0)
        if status < 400 and _runtime_indicates_live_worker(last_runtime):
            print("[worker] local dev worker is accepting live-session work.")
            return True
        time.sleep(0.5)

    print("[worker] local worker setup completed, but runtime did not report accepting before retry; retrying request anyway.")
    return True


def _print_no_live_worker_help(*, args: Any, hub_url: str, payload: dict[str, Any]) -> None:
    print()
    print("[worker] no live worker is connected to this Hub for the requested ring/capability.")
    print(f"[worker] hub={hub_url} ring={payload.get('ring')} capabilities={','.join(payload.get('capabilities') or [])}")
    print("[worker] Open Applications → Worker, keep Dev selected, then use Work now… to complete setup.")
    print("[worker] The Worker page API is the app/control server, usually http://127.0.0.1:8765; port 8771 is legacy standalone worker.")
    print("[worker] The dev faucet warning is separate from this requester smoke; the requester wallet funding import already succeeded.")


def _hub_url_from_args(args: Any) -> str:
    return str(getattr(args, "hub", "") or os.environ.get("MAIN_COMPUTER_HUB_URL") or DEFAULT_HUB_URL).strip().rstrip("/")


def _load_hub_identity(hub_url: str) -> dict[str, Any]:
    for path in ("/api/hub/v1/hub-identity", "/api/hub/v1/topology"):
        status, payload = http_json("GET", hub_url.rstrip("/") + path, timeout=10.0)
        if status < 400 and payload.get("ok") is not False:
            return payload
    raise RuntimeError(f"could not load Hub identity from {hub_url}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit one paid micro-agent chat request to a Main Computer Hub.")
    parser.add_argument("prompt", help="Prompt to send to the worker.")
    parser.add_argument("--hub", default=os.environ.get("MAIN_COMPUTER_HUB_URL", DEFAULT_HUB_URL))
    parser.add_argument(
        "--app",
        default=os.environ.get("MAIN_COMPUTER_APP_URL", DEFAULT_APP_URL),
        help="Override the local Main Computer app/control URL used to auto-prepare a dev worker. Defaults to auto-discovery, usually http://127.0.0.1:8765.",
    )
    parser.add_argument("--ring", default="3")
    parser.add_argument("--capability", default=DEFAULT_CAPABILITY)
    parser.add_argument("--client-node-id", default=DEFAULT_CLIENT_NODE_ID)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-credits", default="2")
    parser.add_argument("--accept-timeout", type=float, default=10.0)
    parser.add_argument("--session-timeout", type=float, default=300.0)
    parser.add_argument("--private-key", default="")
    parser.add_argument("--private-key-file", default="")
    parser.add_argument("--wallet", default="")
    parser.add_argument("--msk-lifetime-minutes", type=int, default=10)
    parser.add_argument("--no-auto-worker", action="store_true", help="Do not try to auto-prepare a local dev worker when the Hub reports no live worker.")
    parser.add_argument("--auto-worker-timeout", type=float, default=10.0)
    parser.add_argument("--auto-worker-seconds", type=int, default=DEFAULT_WORKER_WORK_NOW_SECONDS)
    parser.add_argument("--worker-model", default=os.environ.get("MAIN_COMPUTER_MICRO_AGENT_WORKER_MODEL", DEFAULT_WORKER_MODEL))
    parser.add_argument(
        "--worker-endpoint",
        default=os.environ.get("MAIN_COMPUTER_MICRO_AGENT_WORKER_ENDPOINT", LEGACY_STANDALONE_WORKER_URL),
        help="Callback endpoint advertised in the worker offer. This is separate from --app; the default matches the Worker page contract.",
    )
    parser.add_argument("--worker-credits-per-token", default=DEFAULT_WORKER_CREDITS_PER_TOKEN)
    parser.add_argument("--worker-target-tokens", type=int, default=DEFAULT_WORKER_TARGET_TOKENS)
    parser.add_argument("--worker-availability-mode", default="ai_idle", choices=["ai_idle", "totally_idle"])
    parser.add_argument("--json", action="store_true", help="Print the final response payload as JSON after the text result.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    hub_url = _hub_url_from_args(args)
    print(f"[hub] {hub_url}")
    hub_status = _load_hub_identity(hub_url)
    summary = extract_hub_status_summary(hub_status)
    print(
        f"[hub] ok={hub_status.get('ok', True)} hub_id={summary.get('hub_id')} "
        f"network={summary.get('network_key')} chain_id={summary.get('chain_id')} backend={summary.get('backend')}"
    )

    authorization = request_fresh_multisession_authorization(
        args=args,
        hub_url=hub_url,
        hub_status=hub_status,
        settings={},
    )
    payload = build_work_payload(args, authorization=authorization, hub_status=hub_status)
    print(
        f"[request] ring={payload['ring']} capabilities={','.join(payload['capabilities'])} "
        f"max_price={payload['max_price']['amount']} {payload['max_price']['unit']}"
    )
    status, submitted = http_json("POST", f"{hub_url}/api/hub/v1/work/requests", payload, timeout=30.0)
    print(f"[submit] HTTP {status}")
    if status == 409 and submitted.get("error") == "no_live_worker_available" and not bool(getattr(args, "no_auto_worker", False)):
        try:
            ensure_local_worker_available(args=args, hub_url=hub_url, hub_status=hub_status)
            status, submitted = http_json("POST", f"{hub_url}/api/hub/v1/work/requests", payload, timeout=30.0)
            print(f"[submit] retry HTTP {status}")
        except Exception as exc:
            print(f"[worker] automatic local worker setup failed: {exc}", file=sys.stderr)
            _print_no_live_worker_help(args=args, hub_url=hub_url, payload=payload)
    if status >= 400 or submitted.get("ok") is False:
        if submitted.get("error") == "no_live_worker_available":
            _print_no_live_worker_help(args=args, hub_url=hub_url, payload=payload)
        print(json.dumps(submitted, indent=2, sort_keys=True))
        return 1

    continuation_url = str(submitted.get("continuation_url") or "").strip()
    final_payload = submitted
    if continuation_url:
        final_payload = follow_work_session(continuation_url, poll_timeout=float(args.session_timeout or 300.0))

    text_result = extract_simple_text_result(final_payload)
    if text_result:
        print()
        print("[result]")
        print(text_result)
    else:
        print()
        print("[result] no simple text result found; final payload:")
        print(json.dumps(final_payload, indent=2, sort_keys=True))
    if args.json and text_result:
        print()
        print("[final-payload]")
        print(json.dumps(final_payload, indent=2, sort_keys=True))
    return 0 if str(final_payload.get("status") or submitted.get("status") or "").lower() not in {"failed", "cancelled"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
