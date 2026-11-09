"""Shared Hub dispatch and HTTP server runtime, independent of legacy routes.

P5.3e extracts the formerly legacy-owned dispatcher/server classes. The
legacy Hub route handler is separately owned by ``hub_http_legacy_handler``.
Legacy HubHttpServer callers automatically receive that handler; Dev-Test
installs its own handler explicitly, without importing legacy route code from
this module. No network connection or payment occurs merely by importing it.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from main_computer.config import MainComputerConfig
from main_computer.energy import EnergyCreditLedger
from main_computer.hub_registry_json import HubRegistry
from main_computer.hub_resource_limits import BoundedHubHTTPServer, load_hub_resource_policy
from main_computer.hub_credit_bridge_completion import HubCreditBridgeCompletionService
from main_computer.hub_bridge_backend import build_hub_bridge_backend
from main_computer.hub_credit_indexer import HubCreditIndexer
from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.hub_plex_models import HubAIRequest
from main_computer.hub_plex_service import AIRequestPlexService
from main_computer.ring_admission import load_ring_admission_config
from main_computer.models import ChatMessage, ChatResponse

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


class HubHttpServer(BoundedHubHTTPServer):
    def __init__(
        self, server_address: tuple[str, int], config: MainComputerConfig,
        *, verbose: bool = True, handler_cls: type | None = None,
    ) -> None:
        if handler_cls is None:
            # Legacy callers keep the same HTTP handler. Import at *instance*
            # creation to avoid a module-level cycle with the compatibility facade.
            from main_computer.hub_http_legacy_handler import HubServerHandler
            handler_cls = HubServerHandler
        self.resource_limits = load_hub_resource_policy()
        super().__init__(server_address, handler_cls)
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

