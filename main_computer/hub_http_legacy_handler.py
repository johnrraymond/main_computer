"""Legacy Hub HTTP route implementation, independently owned of the Hub facade.

The route-handler class body is source-identical to P5.3e. Dev-Test inherits
this class but delegates only the explicitly approved P5.3a compatibility routes.
Importing this module does not start a server, contact a Hub or mutate credits.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from http import HTTPStatus
from urllib.parse import parse_qs, urlparse
from typing import Any

from main_computer.credit_units import (
    CREDIT_WEI_PER_CREDIT, credit_wei_product, credit_wei_to_decimal_text, credit_wei_to_display_text,
)
from main_computer.hub_admin_site import HUB_ADMIN_ROUTES, render_hub_admin_html
from main_computer.hub_bridge_backend import HubBridgeBackendError
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.hub_credit_models import (
    DEFAULT_WORKER_PAYOUT_PRECISION_PLACES, stable_id, normalize_worker_payout_precision_places,
)
from main_computer.hub_http_shared import _JsonHandler
from main_computer.hub_http_runtime import HubHttpServer
from main_computer.hub_http_legacy_support import (
    HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID, HUB_MULTISESSION_KEY_MAX_AGE_MINUTES,
    HubCreditAuthorizationError, HubPaymentRequired,
    chat_message_from_dict, load_allfather_hub_remote_manifest,
)
from main_computer.hub_plex_models import HubAIRequest
from main_computer.hub_registry_json import _utc_now
from main_computer.hub_registry_shared import (
    _clean_node_id, _hub_as_bool, _hub_as_int, _hub_credit_wei_from_decimal_text,
)
from main_computer.hub_security import HUB_SECURITY_PROFILE
from main_computer.multisession_key_signing import (
    normalize_address, normalize_chain_id, verify_personal_sign_blob,
)
from main_computer.ring_admission import normalize_requested_ring

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
        if not authorization:
            raise HubCreditAuthorizationError("Worker routes require a multi-session key authorization.")

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
        signed_request = body.get("signed_request")
        if not isinstance(signed_request, dict):
            raise HubCreditAuthorizationError("wallet_signed_revocation_required")
        verification = verify_personal_sign_blob(
            signed_request,
            expected_chain_id=HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID,
            max_age_minutes=HUB_MULTISESSION_KEY_MAX_AGE_MINUTES,
        )
        signed_message = verification.get("message") or {}
        if (
            not signed_message.get("issued_at")
            or signed_message.get("action") != "revoke_multisession_key"
            or signed_message.get("key_id") != key_id
            or signed_message.get("request_id") != "revoke:" + key_id
        ):
            raise HubCreditAuthorizationError("wallet_revocation_intent_mismatch")
        wallet_address = normalize_address(verification["wallet_address"])
        if body.get("wallet_address") is not None and normalize_address(body["wallet_address"]) != wallet_address:
            raise HubCreditAuthorizationError("wallet_revocation_owner_mismatch")
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
        # The legacy Hub has no authenticated requester/admin read principal.
        # Never turn a request/session identifier or a guessed wallet into a
        # bearer capability. Fail closed until dedicated read authorization
        # exists; the authenticated Stable Hub's paid-session API is separate.
        private_legacy_read = (
            path == "/api/hub/v1/admin/bootstrap"
            or path == "/api/hub/v1/metrics"
            or path == "/api/hub/v1/ring-control/feedback-summary"
            or path == "/api/hub/v1/requests"
            or path.startswith("/api/hub/v1/requests/")
            or path == "/api/hub/v1/credits"
            or path.startswith("/api/hub/v1/credits/")
            or path.startswith("/api/hub/v1/bridge/")
            or path in {"/api/hub/payouts", "/api/hub/v1/payouts"}
        )
        if private_legacy_read:
            self._send_json({"ok": False, "error": "authenticated_legacy_read_required"},
                            status=HTTPStatus.FORBIDDEN)
            return
        if path in HUB_ADMIN_ROUTES:
            html = render_hub_admin_html()
            self._send_bytes(html.encode("utf-8"), content_type="text/html; charset=utf-8")
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
            # Public health is intentionally aggregate-only. Registry workers,
            # endpoints, per-worker accounts and finance belong to the Hub.
            snapshot = self.server.registry.status()
            self._send_json({
                "ok": True,
                "api_version": "v1" if path.startswith("/api/hub/v1/") else "legacy",
                "network_key": self.server.config.hub_network,
                "chain_id": self.server.config.chain_id,
                "worker_count": snapshot.get("worker_count", 0),
                "available_worker_count": snapshot.get("available_worker_count", 0),
                "stale_worker_count": snapshot.get("stale_worker_count", 0),
            })
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
            # Public aggregate only: no Worker IDs, endpoints, private metadata.
            snapshot = self.server.registry.status()
            self._send_json({"ok": True,
                             "worker_count": len(snapshot.get("workers", [])),
                             "available_worker_count": snapshot.get("available_worker_count", 0),
                             "stale_worker_count": snapshot.get("stale_worker_count", 0)})
            return
        if path.startswith("/api/hub/v1/workers/"):
            self._send_json({"ok": False, "error": "worker_private_record_forbidden"},
                            status=HTTPStatus.FORBIDDEN)
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
        if not self._preflight_post():
            return
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
                except HubCreditAuthorizationError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
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
            if path == "/api/hub/v1/credits/wallet-funding/complete":
                body = self._read_json()
                self._send_json(self.server.credit_bridge_completion.complete_wallet_funding_deposit(body))
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
                        required=bool(is_worker_pull),
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
