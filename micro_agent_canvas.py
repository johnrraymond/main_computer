#!/usr/bin/env python3
"""
micro_agent_canvas.py

Local micro-agent smoke canvas for Main Computer Hub worker integration.

The script keeps the Hub multi-session-key requirement:
  * it loads the system-created local smoke-client/requester wallet when present;
  * it signs and emits a fresh request_multi_session_key message;
  * for local dev only, it imports deterministic wallet funding from the deployment
    smoke-client manifest so the freshly active MSK has spendable Hub credits;
  * it submits one work request to the live-session worker market and follows the
    returned continuation URL.

No third-party packages are required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from main_computer.multisession_key_signing import (
    build_personal_sign_blob,
    normalize_address,
    normalize_chain_id,
    private_key_to_address,
)


LOCAL_DEV_HUBS = [
    "http://127.0.0.1:8871",
    "http://127.0.0.1:8872",
    "http://127.0.0.1:8873",
    "http://127.0.0.1:8770",
]

PUBLIC_TESTNET_HUB = "https://testnet-hub.greatlibrary.io"
CREDIT_WEI_PER_CREDIT = Decimal("1000000000000000000")


class ResolvedPrivateKey:
    def __init__(
        self,
        *,
        private_key: str,
        wallet_address: str,
        source: str,
        path: str = "",
        deployment: dict[str, Any] | None = None,
    ) -> None:
        self.private_key = private_key
        self.wallet_address = wallet_address
        self.source = source
        self.path = path
        self.deployment = deployment


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Bad JSON in {path}: {exc}") from exc
    return data if isinstance(data, dict) else {}


def find_upwards(filename: str, start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for base in [current, *current.parents]:
        candidate = base / filename
        if candidate.exists():
            return candidate
    return None


def repo_root(start: Path | None = None) -> Path:
    new_patch = find_upwards("new_patch.py", start)
    if new_patch:
        return new_patch.parent
    return (start or Path.cwd()).resolve()


def deep_values(obj: Any, names: set[str]) -> list[str]:
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in names and item not in ("", None):
                    found.append(str(item))
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(obj)
    return found


def unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip().rstrip("/")
        if clean and clean not in seen:
            result.append(clean)
            seen.add(clean)
    return result


def credit_to_wei(value: str | int | float | Decimal) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SystemExit(f"credit amount must be numeric, got {value!r}") from exc
    if amount <= 0:
        raise SystemExit("credit amount must be greater than zero")
    return str(int(amount * CREDIT_WEI_PER_CREDIT))


def http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload, sort_keys=True).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"

    req = Request(url, data=body, headers=headers, method=method)

    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8") or "{}"
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"ok": False, "raw": raw}
            return int(response.status), data if isinstance(data, dict) else {"ok": False, "value": data}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"ok": False, "raw": raw}
        return int(exc.code), data if isinstance(data, dict) else {"ok": False, "value": data}


def get_status(hub_url: str, timeout: float = 5.0) -> tuple[int, dict[str, Any]]:
    return http_json("GET", hub_url.rstrip("/") + "/api/hub/status", timeout=timeout)


def extract_hub_status_summary(status: dict[str, Any]) -> dict[str, str]:
    network = status.get("network") if isinstance(status.get("network"), dict) else {}
    serving = status.get("serving_hub") if isinstance(status.get("serving_hub"), dict) else {}
    hub_id_raw = status.get("hub_id")
    hub_id_record = hub_id_raw if isinstance(hub_id_raw, dict) else {}

    hub_id = (
        str(hub_id_record.get("hub_id") or "").strip()
        or str(serving.get("hub_id") or "").strip()
        or (str(hub_id_raw).strip() if hub_id_raw not in (None, "") and not isinstance(hub_id_raw, dict) else "")
        or str(status.get("serving_hub") or "").strip()
        or "main-computer-hub"
    )
    serving_hub = (
        str(serving.get("hub_id") or "").strip()
        or str(hub_id_record.get("hub_id") or "").strip()
        or hub_id
    )
    network_key = str(status.get("network_key") or network.get("network_key") or status.get("network") or "").strip()
    if isinstance(status.get("network"), dict):
        network_key = str(network.get("network_key") or "").strip()
    chain_id = normalize_chain_id(status.get("chain_id") or network.get("chain_id") or "")
    backend = str(status.get("backend") or status.get("state_backend") or "").strip()
    return {
        "hub_id": hub_id,
        "serving_hub": serving_hub,
        "network_key": network_key,
        "chain_id": chain_id,
        "backend": backend,
    }


def resolve_hub(args: argparse.Namespace, settings: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    candidates: list[str] = []

    if getattr(args, "hub", ""):
        candidates.append(args.hub)

    for env_name in ("MICRO_AGENT_HUB_URL", "MAIN_COMPUTER_HUB_URL", "HUB_URL"):
        if os.environ.get(env_name):
            candidates.append(os.environ[env_name])

    candidates.extend(
        deep_values(
            settings,
            {
                "workerConnectedHubUrl",
                "connected_hub_url",
                "hub_url",
                "hub_public_url",
                "public_url",
            },
        )
    )

    candidates.extend(LOCAL_DEV_HUBS)
    candidates.append(PUBLIC_TESTNET_HUB)

    errors: list[str] = []

    for hub in unique(candidates):
        try:
            status_code, status = get_status(hub, timeout=getattr(args, "status_timeout", 3.0))
        except (URLError, TimeoutError, OSError) as exc:
            errors.append(f"{hub}: {exc}")
            if getattr(args, "hub", ""):
                break
            continue

        if 200 <= status_code < 300 and status.get("ok") is not False:
            return hub.rstrip("/"), status

        errors.append(f"{hub}: HTTP {status_code} {status}")

    raise SystemExit("Could not find a reachable Hub.\n" + "\n".join("  " + item for item in errors[-8:]))


def wallet_json_private_key(payload: dict[str, Any]) -> str:
    for key in ("private_key", "privateKey", "secret_key", "secretKey"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    nested = payload.get("wallet")
    if isinstance(nested, dict):
        return wallet_json_private_key(nested)
    return ""


def wallet_json_address(payload: dict[str, Any]) -> str:
    for key in ("address", "wallet_address", "walletAddress"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    nested = payload.get("wallet")
    if isinstance(nested, dict):
        return wallet_json_address(nested)
    return ""


def load_wallet_file(path: Path) -> tuple[str, str]:
    payload = read_json_file(path)
    private_key = wallet_json_private_key(payload)
    if not private_key:
        raise SystemExit(f"Wallet file {path} does not contain private_key.")
    derived = private_key_to_address(private_key)
    recorded = wallet_json_address(payload)
    if recorded and normalize_address(recorded) != derived:
        raise SystemExit(f"Wallet file {path} address does not match its private key.")
    return private_key, derived


def deployment_latest_paths(root: Path) -> list[Path]:
    return [
        root / "runtime" / "deployments" / "dev" / "latest.json",
        root / "runtime" / "deployments" / "test" / "latest.json",
        root / "runtime" / "deployments" / "testnet" / "latest.json",
        root / "runtime" / "deployments" / "mainnet" / "latest.json",
    ]


def resolve_deployment_for_hub(hub_status: dict[str, Any], root: Path) -> tuple[Path | None, dict[str, Any]]:
    summary = extract_hub_status_summary(hub_status)
    wanted_chain = summary["chain_id"]
    wanted_network = summary["network_key"]

    candidates: list[Path] = []
    if wanted_network:
        candidates.append(root / "runtime" / "deployments" / wanted_network / "latest.json")
    candidates.extend(deployment_latest_paths(root))

    for path in unique([str(p) for p in candidates]):
        candidate = Path(path)
        payload = read_json_file(candidate)
        if not payload:
            continue
        chain = payload.get("chain") if isinstance(payload.get("chain"), dict) else {}
        chain_id = normalize_chain_id(chain.get("chain_id") or payload.get("chain_id") or "")
        env = str(payload.get("environment") or "").strip()
        if wanted_chain and chain_id and chain_id != wanted_chain:
            continue
        if wanted_network and env and env != wanted_network:
            continue
        return candidate, payload

    return None, {}


def resolve_requester_private_key(
    args: argparse.Namespace,
    *,
    hub_status: dict[str, Any],
    settings: dict[str, Any],
    root: Path | None = None,
) -> ResolvedPrivateKey:
    root = root or repo_root()

    cli_private_key = str(getattr(args, "private_key", "") or "").strip()
    if cli_private_key:
        wallet = private_key_to_address(cli_private_key)
        return ResolvedPrivateKey(private_key=cli_private_key, wallet_address=wallet, source="--private-key")

    private_key_file = str(getattr(args, "private_key_file", "") or "").strip()
    if private_key_file:
        path = Path(private_key_file)
        if not path.is_absolute():
            path = root / path
        private_key, wallet = load_wallet_file(path)
        return ResolvedPrivateKey(private_key=private_key, wallet_address=wallet, source="--private-key-file", path=str(path))

    for env_name in (
        "MICRO_AGENT_PRIVATE_KEY",
        "MAIN_COMPUTER_MICRO_AGENT_PRIVATE_KEY",
        "MAIN_COMPUTER_SMOKE_CLIENT_PRIVATE_KEY",
        "MAIN_COMPUTER_PAID_REQUESTER_PRIVATE_KEY",
        "MAIN_COMPUTER_REQUESTER_0_PRIVATE_KEY",
    ):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            wallet = private_key_to_address(value)
            return ResolvedPrivateKey(private_key=value, wallet_address=wallet, source=env_name)

    deployment_path, deployment = resolve_deployment_for_hub(hub_status, root)
    smoke_client = deployment.get("smoke_client") if isinstance(deployment.get("smoke_client"), dict) else {}
    wallet_path_text = str(smoke_client.get("wallet_path") or "").strip()
    if wallet_path_text:
        wallet_path = Path(wallet_path_text)
        if not wallet_path.is_absolute():
            wallet_path = root / wallet_path
        if wallet_path.exists():
            private_key, wallet = load_wallet_file(wallet_path)
            expected = str(smoke_client.get("address") or "").strip()
            if expected and normalize_address(expected) != wallet:
                raise SystemExit(f"Deployment smoke_client address does not match {wallet_path}.")
            return ResolvedPrivateKey(
                private_key=private_key,
                wallet_address=wallet,
                source="deployment smoke-client wallet",
                path=str(wallet_path),
                deployment=deployment,
            )

    raise SystemExit(
        "No requester private key found.\n"
        "Expected the generated deployment smoke-client wallet, or pass --private-key-file/--private-key, "
        "or set MICRO_AGENT_PRIVATE_KEY."
    )


def build_multisession_key_message(
    *,
    wallet_address: str,
    chain_id: str,
    hub_url: str,
    lifetime_minutes: int = 30,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires = now + timedelta(minutes=max(1, int(lifetime_minutes)))
    request_id = "msk_req_" + uuid.uuid4().hex
    return {
        "kind": "main_computer_multisession_key_request",
        "purpose": "request_multi_session_key",
        "wallet_address": normalize_address(wallet_address),
        "chain_id": normalize_chain_id(chain_id),
        "user_slug": "usr_" + uuid.uuid4().hex,
        "request_id": request_id,
        "origin": f"micro-agent-canvas:{hub_url.rstrip('/')}",
        "issued_at": now.isoformat(),
        "expires_at": expires.isoformat(),
    }


def validate_authorization_payload(authorization: dict[str, Any], required_credit_wei: str) -> dict[str, Any]:
    return {
        "wallet_address": authorization.get("wallet_address", ""),
        "multisession_key_id": authorization.get("multisession_key_id", ""),
        "key_id": authorization.get("key_id", authorization.get("multisession_key_id", "")),
        "chain_id": authorization.get("chain_id", ""),
        "required_credit_wei": str(required_credit_wei),
        "multisession_authorization": dict(authorization),
        "payment_authorization": dict(authorization),
    }


def deterministic_dev_funding_payload(
    *,
    wallet_address: str,
    chain_id: str,
    contract_address: str,
    credits_wei: str,
    hub_id: str,
) -> dict[str, Any]:
    wallet = normalize_address(wallet_address)
    chain_dec = normalize_chain_id(chain_id)
    seed = json.dumps(
        {
            "kind": "micro-agent-canvas-dev-wallet-funding-v1",
            "wallet_address": wallet,
            "chain_id": chain_dec,
            "credits_wei": str(credits_wei),
            "hub_id": hub_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()
    return {
        "wallet_address": wallet,
        "chain_id": int(chain_dec),
        "contract_address": normalize_address(contract_address),
        "tx_hash": "0x" + digest,
        "log_index": 0,
        "block_number": 1,
        "payment_asset": "native",
        "payment_amount_base_units": str(max(1, int(credits_wei))),
        "credits_granted_wei": str(max(1, int(credits_wei))),
        "idempotency_key": "micro-agent-canvas-dev-wallet-funding-" + digest[:24],
        "memo": f"micro agent canvas dev funding for {wallet}",
        "metadata": {
            "source": "micro_agent_canvas.py",
            "kind": "local_dev_smoke_client_wallet_funding",
        },
    }


def deployment_bridge_contract_address(deployment: dict[str, Any]) -> str:
    contracts = deployment.get("contracts") if isinstance(deployment.get("contracts"), dict) else {}
    deployments = deployment.get("deployments") if isinstance(deployment.get("deployments"), dict) else {}
    for container in (contracts, deployments):
        record = container.get("hub_credit_bridge_escrow")
        if isinstance(record, dict):
            address = str(record.get("address") or "").strip()
            if address:
                return address
    return "0x" + "0" * 39 + "1"


def maybe_import_dev_wallet_funding(
    *,
    args: argparse.Namespace,
    hub_url: str,
    hub_status: dict[str, Any],
    wallet_address: str,
    required_credit_wei: str,
    resolved_key: ResolvedPrivateKey,
    root: Path | None = None,
) -> dict[str, Any] | None:
    if getattr(args, "skip_dev_funding", False):
        return None

    summary = extract_hub_status_summary(hub_status)
    hub_url_clean = hub_url.rstrip("/")
    is_local_hub = hub_url_clean.startswith("http://127.0.0.1") or hub_url_clean.startswith("http://localhost")
    is_dev = summary["network_key"] == "dev" or summary["chain_id"] == "42424242"

    if not (is_local_hub and is_dev):
        return None

    deployment = resolved_key.deployment or {}
    if not deployment:
        _, deployment = resolve_deployment_for_hub(hub_status, root or repo_root())
    if not deployment:
        return None

    contract_address = deployment_bridge_contract_address(deployment)
    payload = deterministic_dev_funding_payload(
        wallet_address=wallet_address,
        chain_id=summary["chain_id"] or "42424242",
        contract_address=contract_address,
        credits_wei=required_credit_wei,
        hub_id=summary["hub_id"],
    )
    status, result = http_json(
        "POST",
        hub_url_clean + "/api/hub/v1/credits/wallet-funding/import",
        payload,
        timeout=15.0,
    )
    if status >= 400 or result.get("ok") is False:
        raise SystemExit(
            "Dev wallet funding import failed: HTTP "
            + str(status)
            + "\n"
            + json.dumps(result, indent=2, sort_keys=True)
        )
    return result


def request_fresh_multisession_authorization(
    *,
    args: argparse.Namespace,
    hub_url: str,
    hub_status: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    summary = extract_hub_status_summary(hub_status)
    chain_id = summary["chain_id"]
    if not chain_id:
        raise SystemExit("Hub status did not expose chain_id; cannot request an MSK safely.")

    resolved = resolve_requester_private_key(args, hub_status=hub_status, settings=settings)
    wallet_address = normalize_address(getattr(args, "wallet", "") or resolved.wallet_address)

    if wallet_address != resolved.wallet_address:
        raise SystemExit(f"Requested wallet {wallet_address} does not match requester key {resolved.wallet_address}.")

    message = build_multisession_key_message(
        wallet_address=wallet_address,
        chain_id=chain_id,
        hub_url=hub_url,
        lifetime_minutes=int(getattr(args, "msk_lifetime_minutes", 30)),
    )
    signed_request = build_personal_sign_blob(
        message=message,
        private_key=resolved.private_key,
        wallet_address=wallet_address,
        chain_id=chain_id,
    )

    status, requested = http_json(
        "POST",
        hub_url.rstrip("/") + "/api/hub/v1/credits/multisession-keys/request",
        {"signed_request": signed_request},
        timeout=20.0,
    )
    if status >= 400 or requested.get("ok") is False:
        raise SystemExit(
            "Fresh MSK request failed: HTTP "
            + str(status)
            + "\n"
            + json.dumps(requested, indent=2, sort_keys=True)
        )

    key = requested.get("key") if isinstance(requested.get("key"), dict) else {}
    auth_from_hub = requested.get("multisession_authorization")
    if isinstance(auth_from_hub, dict):
        authorization = dict(auth_from_hub)
    else:
        authorization = {}

    key_id = str(
        authorization.get("multisession_key_id")
        or authorization.get("key_id")
        or key.get("id")
        or requested.get("multisession_key_id")
        or ""
    ).strip()
    if not key_id:
        raise SystemExit("Fresh MSK response did not include a key id.")

    authorization.update(
        {
            "kind": "multisession_key",
            "wallet_address": wallet_address,
            "multisession_key_id": key_id,
            "key_id": key_id,
            "chain_id": chain_id,
            "max_authorized_credit_wei": credit_to_wei(getattr(args, "max_credits", "2")),
        }
    )

    required_credit_wei = authorization["max_authorized_credit_wei"]

    funding = maybe_import_dev_wallet_funding(
        args=args,
        hub_url=hub_url,
        hub_status=hub_status,
        wallet_address=wallet_address,
        required_credit_wei=required_credit_wei,
        resolved_key=resolved,
    )
    if funding:
        available = (
            (funding.get("account") if isinstance(funding.get("account"), dict) else {}).get("available_credit_wei")
            or (funding.get("account") if isinstance(funding.get("account"), dict) else {}).get("available_credits_display")
            or ""
        )
        print(f"[funding] dev wallet funding imported/idempotent={funding.get('idempotent', False)} available={available}")

    validate_payload = validate_authorization_payload(authorization, required_credit_wei)
    status, validation = http_json(
        "POST",
        hub_url.rstrip("/") + "/api/hub/v1/credits/multisession-keys/validate",
        validate_payload,
        timeout=15.0,
    )
    if status >= 400 or validation.get("valid") is not True or validation.get("ready") is False:
        raise SystemExit(
            "Fresh MSK validation failed: HTTP "
            + str(status)
            + "\n"
            + json.dumps(validation, indent=2, sort_keys=True)
        )

    return authorization


def normalize_market_ring(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "ring-3"
    if text.startswith("ring-"):
        suffix = text.split("-", 1)[1].strip()
        if suffix and suffix[0].isdigit():
            return "ring-" + suffix[0]
        return text
    match = None
    for char in text:
        if char.isdigit():
            match = char
            break
    if match:
        return "ring-" + match
    return text


def build_work_payload(
    args: argparse.Namespace,
    authorization: dict[str, Any],
    hub_status: dict[str, Any],
) -> dict[str, Any]:
    request_id = "micro_req_" + uuid.uuid4().hex
    prompt = " ".join(getattr(args, "prompt", [])).strip() if isinstance(getattr(args, "prompt", ""), list) else str(getattr(args, "prompt", "") or "").strip()
    prompt = prompt or "Echo from local micro agent canvas."

    summary = extract_hub_status_summary(hub_status)
    ring = normalize_market_ring(getattr(args, "ring", "ring-3"))
    capabilities = list(getattr(args, "capability", None) or ["chat.completions"])

    metadata: dict[str, Any] = {
        "micro_agent": "local-canvas-v1",
        "execution_mode": "exp-live-session-worker-v1",
        "hub_chain_id": summary["chain_id"],
        "hub_id": summary["hub_id"],
    }

    if authorization:
        metadata["auth_mode"] = "multisession-wallet"
        metadata["multisession_key_id"] = authorization.get("multisession_key_id")
        metadata["wallet_address"] = authorization.get("wallet_address")

    payload: dict[str, Any] = {
        "request_id": request_id,
        "idempotency_key": request_id,
        "client_node_id": getattr(args, "client_node_id", "micro-agent-local"),
        "model": getattr(args, "model", "micro-agent-local"),
        "ring": ring,
        "partition": ring,
        "capabilities": capabilities,
        "required_capabilities": capabilities,
        "max_credits": getattr(args, "max_credits", "2"),
        "max_price": {"amount": str(getattr(args, "max_credits", "2")), "unit": "compute_credit"},
        "accept_timeout_seconds": float(getattr(args, "accept_timeout", 10.0)),
        "input": {
            "kind": capabilities[0] if capabilities else "chat.completions",
            "value": prompt,
            "prompt": prompt,
        },
        "messages": [{"role": "user", "content": prompt}],
        "metadata": metadata,
    }

    if authorization:
        payload["multisession_authorization"] = dict(authorization)
        payload["payment_authorization"] = dict(authorization)

    return payload


def extract_result_text(session_payload: dict[str, Any]) -> str:
    accepted = session_payload.get("accepted_session")
    if not isinstance(accepted, dict):
        accepted = session_payload.get("session") if isinstance(session_payload.get("session"), dict) else {}

    worker_result = accepted.get("worker_result")
    if isinstance(worker_result, dict):
        result = worker_result.get("result")
        if isinstance(result, dict):
            response = result.get("response")
            if isinstance(response, dict) and response.get("content") is not None:
                return str(response.get("content"))
            if result.get("content") is not None:
                return str(result.get("content"))
            if result.get("value") is not None:
                return str(result.get("value"))
        if worker_result.get("content") is not None:
            return str(worker_result.get("content"))

    for key in ("result", "output", "response"):
        value = accepted.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for nested_key in ("content", "text", "value"):
                if value.get(nested_key) is not None:
                    return str(value.get(nested_key))

    worker_failure = accepted.get("worker_failure")
    if isinstance(worker_failure, dict):
        return "WORKER FAILED: " + json.dumps(worker_failure, indent=2, sort_keys=True)

    return ""


def wait_for_result(continuation_url: str, *, timeout: float, interval: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_status = ""
    last_payload: dict[str, Any] = {}

    while time.time() < deadline:
        status_code, payload = http_json("GET", continuation_url, timeout=min(10.0, interval + 5.0))
        last_payload = payload

        accepted = payload.get("accepted_session") if isinstance(payload.get("accepted_session"), dict) else {}
        status = str(payload.get("status") or accepted.get("status") or "")

        if status and status != last_status:
            print(f"[session] status={status}")
            last_status = status

        if status in {"succeeded", "failed", "cancelled"}:
            return payload

        if status_code >= 400:
            print(f"[session] HTTP {status_code}: {json.dumps(payload, sort_keys=True)}")

        time.sleep(interval)

    raise SystemExit(
        f"Timed out waiting for worker result after {timeout:.1f}s.\n"
        f"Last session payload:\n{json.dumps(last_payload, indent=2, sort_keys=True)}"
    )


def continuation_url_from_submit(hub_url: str, submitted: dict[str, Any]) -> str:
    continuation_url = str(submitted.get("continuation_url") or "").strip()
    if continuation_url:
        return continuation_url

    continuation = submitted.get("continuation") if isinstance(submitted.get("continuation"), dict) else {}
    stream_path = str(continuation.get("stream_path") or continuation.get("url_path") or "").strip()
    hub_for_stream = str(continuation.get("hub_url") or hub_url).rstrip("/")
    if stream_path:
        return hub_for_stream + stream_path

    session_url = str(submitted.get("session_url") or "").strip()
    if session_url:
        return session_url

    return ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Submit one local micro-agent request through the Main Computer Hub live-worker path."
    )
    parser.add_argument("prompt", nargs="*", help="Prompt/text to send to the local worker.")
    parser.add_argument("--hub", help="Hub URL. Defaults to env/settings/local dev Hub discovery.")
    parser.add_argument("--wallet", help="Requester wallet address. Must match the selected private key.")
    parser.add_argument("--private-key", default="", help="Requester private key. Defaults to deployment smoke-client wallet.")
    parser.add_argument("--private-key-file", default="", help="Requester wallet JSON/private-key file.")
    parser.add_argument("--skip-dev-funding", action="store_true", help="Do not import deterministic local-dev wallet funding before validation.")
    parser.add_argument("--ring", default="ring-3", help="Worker market ring/partition to target. Default: ring-3.")
    parser.add_argument("--capability", action="append", default=None, help="Required worker capability. Repeatable. Default: chat.completions.")
    parser.add_argument("--model", default="micro-agent-local", help="Model label to put in the Hub request.")
    parser.add_argument("--client-node-id", default="micro-agent-local", help="Requester/client node id.")
    parser.add_argument("--max-credits", default="2", help="Max compute-credit budget in human credits. Default: 2.")
    parser.add_argument("--msk-lifetime-minutes", type=int, default=30, help="Fresh MSK lifetime.")
    parser.add_argument("--accept-timeout", type=float, default=10.0, help="Hub worker accept timeout seconds.")
    parser.add_argument("--timeout", type=float, default=60.0, help="Seconds to wait for worker result.")
    parser.add_argument("--poll-interval", type=float, default=0.75, help="Seconds between continuation polls.")
    parser.add_argument("--status-timeout", type=float, default=3.0, help="Seconds for Hub status probes.")
    parser.add_argument("--dump-json", action="store_true", help="Print full submit/session JSON.")
    args = parser.parse_args()

    root = repo_root()
    settings_path = find_upwards("worker_settings.json", root)
    settings = read_json_file(settings_path) if settings_path else {}

    hub_url, hub_status = resolve_hub(args, settings)
    summary = extract_hub_status_summary(hub_status)

    print(f"[hub] {hub_url}")
    print(
        "[hub] "
        f"ok={hub_status.get('ok')} "
        f"hub_id={summary['hub_id']} "
        f"network={summary['network_key']} "
        f"chain_id={summary['chain_id']} "
        f"backend={summary['backend']}"
    )

    authorization = request_fresh_multisession_authorization(
        args=args,
        hub_url=hub_url,
        hub_status=hub_status,
        settings=settings,
    )

    wallet = str(authorization["wallet_address"])
    print(
        "[auth] "
        f"wallet={wallet[:10]}…{wallet[-6:]} "
        f"msk={str(authorization['multisession_key_id'])[:10]}… "
        f"chain_id={authorization.get('chain_id')}"
    )

    payload = build_work_payload(args, authorization, hub_status)
    print(
        "[request] "
        f"ring={payload['ring']} "
        f"capabilities={','.join(payload['capabilities'])} "
        f"max_price={payload['max_price']['amount']} {payload['max_price']['unit']}"
    )

    submit_url = hub_url.rstrip("/") + "/api/hub/v1/work/requests"
    status_code, submitted = http_json("POST", submit_url, payload, timeout=20.0)

    print(f"[submit] HTTP {status_code}")

    if args.dump_json or status_code >= 400 or submitted.get("ok") is False:
        print(json.dumps(submitted, indent=2, sort_keys=True))

    if status_code >= 400 or submitted.get("ok") is False:
        return 2

    continuation_url = continuation_url_from_submit(hub_url, submitted)
    if not continuation_url:
        print("[submit] accepted, but no continuation_url returned")
        print(json.dumps(submitted, indent=2, sort_keys=True))
        return 0

    print(f"[session] {continuation_url}")

    final_payload = wait_for_result(
        continuation_url,
        timeout=args.timeout,
        interval=args.poll_interval,
    )

    result_text = extract_result_text(final_payload)

    if result_text:
        print("\n[result]")
        print(result_text)
    else:
        print("\n[result] no simple text result found; final payload:")
        print(json.dumps(final_payload, indent=2, sort_keys=True))

    if args.dump_json:
        print("\n[final-json]")
        print(json.dumps(final_payload, indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
