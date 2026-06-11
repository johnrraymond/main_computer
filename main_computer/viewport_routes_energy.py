from __future__ import annotations

from dataclasses import replace
import ipaddress
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from main_computer.viewport_state import *  # noqa: F401,F403
from main_computer.hub_networks import HubNetworkConfigError, load_hub_network_registry

class ViewportEnergyRoutesMixin:
    def _handle_energy_register_node(self) -> None:
        try:
            body = self._read_json()
            if not self._energy_passcode_ok(body):
                self._send_json({"error": "Energy admin passcode is required."}, status=HTTPStatus.FORBIDDEN)
                return
            report = self.server.energy_ledger.register_node(
                node_id=str(body.get("node_id", "")),
                role=str(body.get("role", "worker")),
                endpoint=str(body.get("endpoint", "")),
            )
            self.server.signal("api-energy-register-node", node_id=body.get("node_id", ""))
            self._send_json(report)
        except Exception as exc:
            self.server.signal("api-energy-register-node-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_energy_issue(self) -> None:
        try:
            body = self._read_json()
            if not self._energy_passcode_ok(body):
                self._send_json({"error": "Energy admin passcode is required."}, status=HTTPStatus.FORBIDDEN)
                return
            report = self.server.energy_ledger.issue(
                node_id=str(body.get("node_id", "")),
                credits=int(body.get("credits", 0)),
                memo=str(body.get("memo", "")),
            )
            self.server.signal("api-energy-issue", node_id=body.get("node_id", ""), credits=body.get("credits", 0))
            self._send_json(report)
        except Exception as exc:
            self.server.signal("api-energy-issue-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_energy_spend(self) -> None:
        try:
            body = self._read_json()
            if not self._energy_passcode_ok(body):
                self._send_json({"error": "Energy admin passcode is required."}, status=HTTPStatus.FORBIDDEN)
                return
            report = self.server.energy_ledger.spend(
                node_id=str(body.get("node_id", "")),
                credits=int(body.get("credits", 0)),
                memo=str(body.get("memo", "")),
            )
            self.server.signal("api-energy-spend", node_id=body.get("node_id", ""), credits=body.get("credits", 0))
            self._send_json(report)
        except Exception as exc:
            self.server.signal("api-energy-spend-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_hub_config_status(self) -> None:
        self.server.signal("api-hub-config-status")
        self._send_json(self._hub_config_payload())

    def _handle_hub_config_save(self) -> None:
        try:
            body = self._read_json()
            if not self._energy_passcode_ok(body):
                self._send_json({"error": "Energy admin passcode is required."}, status=HTTPStatus.FORBIDDEN)
                return

            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            hub_client_node_id = str(body.get("hub_client_node_id") or self.server.config.hub_client_node_id).strip()
            if not hub_client_node_id:
                raise ValueError("Hub client node id is required.")
            hub_high_security = self._coerce_bool(body.get("hub_high_security"), default=self.server.config.hub_high_security)
            try:
                hub_timeout_s = max(1.0, float(body.get("hub_timeout_s", self.server.config.hub_timeout_s)))
            except (TypeError, ValueError) as exc:
                raise ValueError("Hub timeout must be a number.") from exc

            new_config = replace(
                self.server.config,
                hub_url=hub_url,
                hub_client_node_id=hub_client_node_id,
                hub_high_security=hub_high_security,
                hub_timeout_s=hub_timeout_s,
            )
            self.server.config = new_config

            saved = self._save_hub_config(
                {
                    "hub_url": hub_url,
                    "hub_client_node_id": hub_client_node_id,
                    "hub_high_security": hub_high_security,
                    "hub_timeout_s": hub_timeout_s,
                    "upstream_hub_url": self._clean_hub_url(str(body.get("upstream_hub_url") or ""), allow_empty=True),
                }
            )

            connect_report = None
            if self._coerce_bool(body.get("connect_upstream"), default=False):
                upstream_hub_url = saved.get("upstream_hub_url", "")
                if not upstream_hub_url:
                    raise ValueError("Upstream hub URL is required to connect an upstream hub.")
                connect_report = self._register_upstream_hub(
                    local_hub_url=hub_url,
                    upstream_hub_url=str(upstream_hub_url),
                    node_id=str(body.get("upstream_node_id") or "upstream-hub"),
                    credits_per_request=int(body.get("upstream_credits_per_request", self.server.config.hub_credits_per_request) or 1),
                )

            payload = self._hub_config_payload()
            payload["saved"] = saved
            payload["connect_report"] = connect_report
            self.server.signal(
                "api-hub-config-save",
                provider=self.server.config.provider,
                hub_url=hub_url,
                connected=bool(connect_report),
            )
            self._send_json(payload)
        except Exception as exc:
            self.server.signal("api-hub-config-save-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _worker_ui_client_is_local(self) -> bool:
        host = self.client_address[0] if self.client_address else ""
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return host.lower() in {"localhost"}


    def _worker_settings_path(self) -> Path:
        return self.server.debug_root / "worker_settings.json"

    def _sanitize_worker_settings(self, value: Any) -> dict[str, Any]:
        settings = value.get("settings") if isinstance(value, dict) and isinstance(value.get("settings"), dict) else value
        if not isinstance(settings, dict):
            settings = {}

        def boolish(raw: Any, default: bool = False) -> bool:
            if isinstance(raw, bool):
                return raw
            text = str(raw or "").strip().lower()
            if text in {"1", "true", "yes", "on", "enabled", "enable"}:
                return True
            if text in {"0", "false", "no", "off", "disabled", "disable"}:
                return False
            return bool(default)

        def intish(raw: Any, default: int, *, minimum: int = 0, maximum: int = 1_000_000_000) -> int:
            try:
                number = int(raw)
            except (TypeError, ValueError):
                number = default
            return min(maximum, max(minimum, number))

        def text(raw: Any, default: str = "") -> str:
            return str(raw if raw is not None else default).strip()

        def jsonable(raw: Any, default: Any) -> Any:
            try:
                value = json.loads(json.dumps(raw, ensure_ascii=False))
            except (TypeError, ValueError):
                return default
            return value if isinstance(value, type(default)) else default

        selected_network = text(settings.get("selectedNetwork", settings.get("selected_network")), "none").lower()
        if selected_network not in {"mainnet", "testnet", "test", "dev", "none"}:
            selected_network = "none"
        requested_ring = text(settings.get("workerRequestedRing", settings.get("worker_requested_ring")), "2")
        if requested_ring not in {"0", "1", "2"}:
            requested_ring = "2"
        connection_status = text(settings.get("workerConnectionStatus", settings.get("worker_connection_status")), "disconnected")
        if connection_status not in {"disconnected", "connecting", "connected", "failed", "stale"}:
            connection_status = "disconnected"
        signed_connection = settings.get("signedWorkerConnection", settings.get("signed_worker_connection"))
        if isinstance(signed_connection, dict):
            signed_assigned_ring = text(signed_connection.get("assigned_ring"), "")
            if signed_assigned_ring not in {"0", "1", "2"}:
                signed_assigned_ring = ""
            signed_connection = {
                "network": text(signed_connection.get("network"), selected_network),
                "requested_ring": text(signed_connection.get("requested_ring"), requested_ring),
                "wallet_address": text(signed_connection.get("wallet_address"), ""),
                "credit_wallet": text(signed_connection.get("credit_wallet"), ""),
                "hub_url": self._clean_hub_url(text(signed_connection.get("hub_url"), ""), allow_empty=True),
                "chain_id": text(signed_connection.get("chain_id"), ""),
                "message": text(signed_connection.get("message"), ""),
                "signature": text(signed_connection.get("signature"), ""),
                "signed_at": text(signed_connection.get("signed_at"), ""),
                "status": text(signed_connection.get("status"), "signed"),
                "hub_registered": boolish(signed_connection.get("hub_registered"), False),
                "assigned_ring": signed_assigned_ring,
                "worker_id": text(signed_connection.get("worker_id"), ""),
                "pricing_policy": text(signed_connection.get("pricing_policy"), ""),
                "hub_registration": jsonable(signed_connection.get("hub_registration"), {}),
                "worker": jsonable(signed_connection.get("worker"), {}),
                "pool": jsonable(signed_connection.get("pool"), {}),
            }
        else:
            signed_connection = {}

        assigned_ring = text(settings.get("workerAssignedRing", settings.get("worker_assigned_ring")), "")
        if assigned_ring not in {"0", "1", "2"}:
            assigned_ring = ""
        cleaned: dict[str, Any] = {
            "selectedNetwork": selected_network,
            "workerRequestedRing": requested_ring,
            "workerAssignedRing": assigned_ring,
            "workerRegisteredId": text(settings.get("workerRegisteredId", settings.get("worker_registered_id")), ""),
            "workerPricingPolicy": text(settings.get("workerPricingPolicy", settings.get("worker_pricing_policy")), ""),
            "workerHubRegistration": jsonable(settings.get("workerHubRegistration", settings.get("worker_hub_registration")), {}),
            "workerPool": jsonable(settings.get("workerPool", settings.get("worker_pool")), {}),
            "workerConnectionStatus": connection_status,
            "workerConnectedAt": text(settings.get("workerConnectedAt", settings.get("worker_connected_at")), ""),
            "workerConnectionError": text(settings.get("workerConnectionError", settings.get("worker_connection_error")), ""),
            "workerConnectedHubUrl": self._clean_hub_url(text(settings.get("workerConnectedHubUrl", settings.get("worker_connected_hub_url")), ""), allow_empty=True),
            "signedWorkerConnection": signed_connection,
            "remoteEnabled": boolish(settings.get("remoteEnabled", settings.get("remote_enabled")), False),
            "remoteMode": text(settings.get("remoteMode", settings.get("remote_mode")), "ask-when-busy"),
            "remoteCreditsPerToken": text(settings.get("remoteCreditsPerToken", settings.get("remote_credits_per_token")), "0.001"),
            "remoteMaxOutputTokens": intish(settings.get("remoteMaxOutputTokens", settings.get("remote_max_output_tokens")), 1024, minimum=1, maximum=128_000),
            "remoteDailyLimit": intish(settings.get("remoteDailyLimit", settings.get("remote_daily_limit")), 100000, minimum=0),
            "remoteAskBeforeSpend": boolish(settings.get("remoteAskBeforeSpend", settings.get("remote_ask_before_spend")), False),
            "remoteOnlyWhenBusy": boolish(settings.get("remoteOnlyWhenBusy", settings.get("remote_only_when_busy")), False),
            "sellerEnabled": boolish(settings.get("sellerEnabled", settings.get("seller_enabled")), False),
            "rentalEnabled": boolish(settings.get("rentalEnabled", settings.get("rental_enabled")), False),
            "lockAiModel": boolish(settings.get("lockAiModel", settings.get("lock_ai_model")), False),
            "registrationHubUrl": self._clean_hub_url(text(settings.get("registrationHubUrl", settings.get("registration_hub_url")), self.server.config.hub_url), allow_empty=True),
            "nodeId": text(settings.get("nodeId", settings.get("node_id")), "local-worker-001"),
            "endpoint": text(settings.get("endpoint"), "http://127.0.0.1:8771"),
            "models": text(settings.get("models"), ""),
            "capability": text(settings.get("capability"), "chat.completions"),
            "creditsPerRequest": intish(settings.get("creditsPerRequest", settings.get("credits_per_request")), 5500123, minimum=0),
            "maxConcurrency": intish(settings.get("maxConcurrency", settings.get("max_concurrency")), 1, minimum=1, maximum=1024),
            "executionMode": text(settings.get("executionMode", settings.get("execution_mode")), "worker_pull_v0"),
        }
        hubs = settings.get("hubs")
        if isinstance(hubs, list):
            cleaned["hubs"] = [
                {
                    "name": text(hub.get("name"), "Hub"),
                    "url": text(hub.get("url"), ""),
                    "role": text(hub.get("role"), "use-provide"),
                }
                for hub in hubs
                if isinstance(hub, dict) and (text(hub.get("name")) or text(hub.get("url")))
            ]
        else:
            cleaned["hubs"] = []
        return cleaned

    def _load_worker_settings(self) -> dict[str, Any]:
        path = self._worker_settings_path()
        try:
            if not path.exists():
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
            return self._sanitize_worker_settings(data)
        except Exception:
            return {}

    def _save_worker_settings(self, settings: dict[str, Any], *, changed_fields: list[str] | None = None) -> dict[str, Any]:
        incoming = self._sanitize_worker_settings(settings)
        allowed_changes = {str(field or "").strip() for field in (changed_fields or []) if str(field or "").strip()}
        if allowed_changes:
            cleaned = self._sanitize_worker_settings(self._load_worker_settings())
            for key in allowed_changes:
                if key in incoming:
                    cleaned[key] = incoming[key]
            cleaned = self._sanitize_worker_settings(cleaned)
        else:
            cleaned = incoming
        path = self._worker_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return cleaned

    def _handle_worker_settings_load(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker settings are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            settings = self._load_worker_settings()
            self.server.signal("api-worker-settings-load", saved=bool(settings), remote_enabled=bool(settings.get("remoteEnabled")))
            self._send_json({"ok": True, "settings": settings})
        except Exception as exc:
            self.server.signal("api-worker-settings-load-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_settings_save(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker settings are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            changed_fields = body.get("changed_fields") if isinstance(body, dict) else None
            if not isinstance(changed_fields, list):
                changed_fields = None
            settings = self._save_worker_settings(body, changed_fields=changed_fields)
            self.server.signal("api-worker-settings-save", remote_enabled=bool(settings.get("remoteEnabled")))
            self._send_json({"ok": True, "settings": settings})
        except Exception as exc:
            self.server.signal("api-worker-settings-save-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_hub_health(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker hub checks are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            status = self._fetch_hub_status(hub_url)
            self.server.signal("api-worker-hub-health", hub_url=hub_url, reachable=status.get("reachable"))
            self._send_json({"ok": True, "hub_url": hub_url, "status": status, "reachable": bool(status.get("reachable"))})
        except Exception as exc:
            self.server.signal("api-worker-hub-health-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _worker_network_order(self) -> list[str]:
        return ["mainnet", "testnet", "test", "dev"]

    def _worker_ring_order(self) -> list[dict[str, str]]:
        return [
            {"ring": "0", "label": "Ring 0 - Operator", "description": "operator / direct whitelist"},
            {"ring": "1", "label": "Ring 1 - Protected", "description": "protected trusted workers"},
            {"ring": "2", "label": "Ring 2 - Public", "description": "public/default workers"},
        ]

    def _worker_network_profile_payload(self, profile: Any) -> dict[str, Any]:
        return {
            "network": profile.network_key,
            "network_key": profile.network_key,
            "display_name": profile.display_name,
            "kind": profile.kind,
            "chain_id": profile.chain_id,
            "chain_rpc_url": profile.chain_rpc_url or "",
            "hub_url": profile.hub_url,
            "hub_public_url": profile.hub_public_url or profile.hub_url,
            "hub_bind_host": profile.hub_bind_host,
            "hub_bind_port": profile.hub_bind_port,
            "deployment_manifest_path": str(profile.deployment_manifest_path or ""),
        }

    def _worker_network_profiles_payload(self) -> list[dict[str, Any]]:
        registry = load_hub_network_registry()
        profiles: list[dict[str, Any]] = []
        for key in self._worker_network_order():
            if key not in registry.networks:
                continue
            profiles.append(self._worker_network_profile_payload(registry.get(key)))
        return profiles

    def _normalize_worker_network_key(self, value: Any, *, allow_none: bool = True) -> str:
        key = str(value or "none").strip().lower()
        allowed = set(self._worker_network_order())
        if allow_none:
            allowed.add("none")
        if key not in allowed:
            available = ", ".join([*self._worker_network_order(), "none"] if allow_none else self._worker_network_order())
            raise ValueError(f"Unknown worker network {key!r}. Available networks: {available}.")
        return key

    def _normalize_worker_ring(self, value: Any) -> str:
        ring = str(value if value is not None else "2").strip()
        if ring not in {"0", "1", "2"}:
            raise ValueError("Worker ring must be one of 0, 1, or 2.")
        return ring

    def _worker_network_session_payload(self, settings: dict[str, Any], *, check_hub: bool = False) -> dict[str, Any]:
        profiles = self._worker_network_profiles_payload()
        profiles_by_key = {str(profile["network"]): profile for profile in profiles}
        selected = self._normalize_worker_network_key(settings.get("selectedNetwork", "none"))
        requested_ring = self._normalize_worker_ring(settings.get("workerRequestedRing", "2"))
        signed_connection = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        hub_registration = settings.get("workerHubRegistration") if isinstance(settings.get("workerHubRegistration"), dict) else {}
        worker_pool = settings.get("workerPool") if isinstance(settings.get("workerPool"), dict) else {}
        signed_worker = signed_connection.get("worker") if isinstance(signed_connection.get("worker"), dict) else {}
        assigned_ring = str(
            settings.get("workerAssignedRing")
            or signed_connection.get("assigned_ring")
            or signed_worker.get("assigned_ring")
            or ""
        )
        worker_id = str(
            settings.get("workerRegisteredId")
            or signed_connection.get("worker_id")
            or signed_worker.get("worker_id")
            or signed_worker.get("node_id")
            or ""
        )
        pricing_policy = str(
            settings.get("workerPricingPolicy")
            or signed_connection.get("pricing_policy")
            or signed_worker.get("pricing_policy")
            or ""
        )

        session: dict[str, Any] = {
            "selected_network": selected,
            "connection_status": "disconnected" if selected == "none" else str(settings.get("workerConnectionStatus") or "stale"),
            "requested_ring": requested_ring,
            "assigned_ring": assigned_ring,
            "worker_id": worker_id,
            "pricing_policy": pricing_policy,
            "connected_at": str(settings.get("workerConnectedAt") or ""),
            "connection_error": str(settings.get("workerConnectionError") or ""),
            "connected_hub_url": str(settings.get("workerConnectedHubUrl") or ""),
            "signed_connection": signed_connection,
            "hub_registration": hub_registration or None,
            "worker_pool": worker_pool or None,
            "profile": None,
            "hub_status": None,
        }

        if selected != "none":
            profile = profiles_by_key.get(selected)
            if not profile:
                session["connection_status"] = "failed"
                session["connection_error"] = f"Selected worker network {selected!r} is not present in the Hub network registry."
            else:
                session["profile"] = profile
                hub_url = str(profile.get("hub_url") or "")
                if check_hub:
                    status = self._fetch_hub_status(hub_url)
                    session["hub_status"] = status
                    session["connected_hub_url"] = hub_url
                    if status.get("reachable"):
                        session["connection_status"] = "connected"
                        session["connection_error"] = ""
                    else:
                        session["connection_status"] = "failed"
                        session["connection_error"] = str(status.get("error") or "Hub is unreachable.")
                elif not session["connected_hub_url"]:
                    session["connected_hub_url"] = hub_url

        return {
            "ok": True,
            "networks": profiles,
            "network_order": self._worker_network_order(),
            "rings": self._worker_ring_order(),
            "session": session,
        }

    def _handle_worker_network_session_load(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker network sessions are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            settings = self._load_worker_settings()
            payload = self._worker_network_session_payload(settings, check_hub=bool(settings.get("selectedNetwork") not in {None, "", "none"}))
            session = payload["session"]
            if session["selected_network"] != "none":
                settings["workerConnectionStatus"] = session["connection_status"]
                settings["workerConnectedHubUrl"] = session.get("connected_hub_url", "")
                settings["workerConnectionError"] = session.get("connection_error", "")
                if session["connection_status"] == "connected":
                    settings["workerConnectedAt"] = worker_now = datetime.now(timezone.utc).isoformat()
                    session["connected_at"] = worker_now
                self._save_worker_settings(settings)
            self.server.signal("api-worker-network-session-load", selected=session["selected_network"], status=session["connection_status"])
            self._send_json(payload)
        except Exception as exc:
            self.server.signal("api-worker-network-session-load-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_network_session_select(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker network sessions are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            selected = self._normalize_worker_network_key(body.get("network"))
            requested_ring = self._normalize_worker_ring(body.get("requested_ring", "2"))
            settings = self._load_worker_settings()
            settings["selectedNetwork"] = selected
            settings["workerRequestedRing"] = requested_ring
            settings["workerAssignedRing"] = ""
            settings["workerRegisteredId"] = ""
            settings["workerPricingPolicy"] = ""
            settings["workerHubRegistration"] = {}
            settings["workerPool"] = {}
            settings["signedWorkerConnection"] = {}
            if selected == "none":
                settings.update(
                    {
                        "workerConnectionStatus": "disconnected",
                        "workerConnectedAt": "",
                        "workerConnectionError": "",
                        "workerConnectedHubUrl": "",
                        "workerAssignedRing": "",
                        "workerRegisteredId": "",
                        "workerPricingPolicy": "",
                        "workerHubRegistration": {},
                        "workerPool": {},
                    }
                )
                saved = self._save_worker_settings(settings)
                payload = self._worker_network_session_payload(saved, check_hub=False)
                self.server.signal("api-worker-network-disconnect")
                self._send_json(payload)
                return

            payload = self._worker_network_session_payload(settings, check_hub=True)
            session = payload["session"]
            settings["workerConnectionStatus"] = session["connection_status"]
            settings["workerConnectedHubUrl"] = session.get("connected_hub_url", "")
            settings["workerConnectionError"] = session.get("connection_error", "")
            settings["workerConnectedAt"] = datetime.now(timezone.utc).isoformat() if session["connection_status"] == "connected" else ""
            saved = self._save_worker_settings(settings)
            payload = self._worker_network_session_payload(saved, check_hub=False)
            payload["session"]["hub_status"] = session.get("hub_status")
            self.server.signal("api-worker-network-select", selected=selected, status=payload["session"]["connection_status"])
            self._send_json(payload)
        except Exception as exc:
            self.server.signal("api-worker-network-select-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_network_connect_order_sign(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker network connection orders are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            selected = self._normalize_worker_network_key(body.get("network"), allow_none=False)
            requested_ring = self._normalize_worker_ring(body.get("requested_ring", "2"))
            wallet_address = self._normalize_worker_wallet_address(body.get("wallet_address"))
            signature = str(body.get("signature") or "").strip()
            message = str(body.get("message") or "").strip()
            if not signature:
                raise ValueError("signature is required.")
            if not message:
                raise ValueError("message is required.")

            worker_payload = body.get("worker")
            if not isinstance(worker_payload, dict):
                raise ValueError("worker registration payload is required.")
            registration_payload = self._worker_registration_payload_from_ui(worker_payload)

            settings = self._load_worker_settings()
            current = self._normalize_worker_network_key(settings.get("selectedNetwork", "none"))
            if current != selected:
                raise ValueError(f"Cannot sign for {selected!r}; current worker network is {current!r}.")

            session_payload = self._worker_network_session_payload(settings, check_hub=False)
            profile = session_payload["session"].get("profile") if isinstance(session_payload.get("session"), dict) else None
            profile = profile if isinstance(profile, dict) else {}
            hub_url = self._clean_hub_url(str(body.get("hub_url") or profile.get("hub_url") or self.server.config.hub_url))
            profile_hub_url = str(profile.get("hub_url") or "").strip()
            if profile_hub_url and hub_url != self._clean_hub_url(profile_hub_url):
                raise ValueError(f"Signed worker connect order hub {hub_url!r} does not match selected network hub {profile_hub_url!r}.")
            chain_id = str(profile.get("chain_id") or body.get("chain_id") or "")

            signed_connection = {
                "network": selected,
                "requested_ring": requested_ring,
                "wallet_address": wallet_address,
                "credit_wallet": wallet_address,
                "hub_url": hub_url,
                "chain_id": chain_id,
                "message": message,
                "signature": signature,
                "signed_at": datetime.now(timezone.utc).isoformat(),
                "status": "registering-with-hub",
            }
            registration = self._post_worker_connect_order_to_hub(
                hub_url=hub_url,
                payload={
                    "signed_connection": signed_connection,
                    "worker": registration_payload,
                },
            )
            worker = registration.get("worker") if isinstance(registration.get("worker"), dict) else {}
            pool = registration.get("pool") if isinstance(registration.get("pool"), dict) else {}
            assigned_ring = str(registration.get("assigned_ring") or worker.get("assigned_ring") or requested_ring)
            worker_id = str(registration.get("worker_id") or worker.get("worker_id") or worker.get("node_id") or registration_payload["node_id"])
            pricing_policy = str(registration.get("pricing_policy") or worker.get("pricing_policy") or worker.get("capabilities", {}).get("pricing_policy", ""))
            signed_connection.update(
                {
                    "status": "hub-registered",
                    "hub_registered": True,
                    "assigned_ring": assigned_ring,
                    "worker_id": worker_id,
                    "pricing_policy": pricing_policy,
                    "hub_registration": registration,
                    "worker": worker,
                    "pool": pool,
                }
            )
            settings["workerRequestedRing"] = requested_ring
            settings["workerAssignedRing"] = assigned_ring
            settings["workerRegisteredId"] = worker_id
            settings["workerPricingPolicy"] = pricing_policy
            settings["workerConnectedHubUrl"] = hub_url
            settings["workerConnectionStatus"] = "connected"
            settings["workerConnectionError"] = ""
            settings["workerConnectedAt"] = datetime.now(timezone.utc).isoformat()
            settings["workerHubRegistration"] = registration
            settings["workerPool"] = pool
            settings["signedWorkerConnection"] = signed_connection
            saved = self._save_worker_settings(settings)
            payload = self._worker_network_session_payload(saved, check_hub=False)
            self.server.signal(
                "api-worker-network-connect-order-hub-register",
                selected=selected,
                ring=requested_ring,
                assigned_ring=assigned_ring,
                wallet=wallet_address,
                worker_id=worker_id,
            )
            self._send_json(payload)
        except Exception as exc:
            self.server.signal("api-worker-network-connect-order-sign-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _worker_multisession_key_cache_path(self) -> Path:
        return self.server.debug_root / "worker_multisession_keys.json"

    def _normalize_worker_wallet_address(self, value: Any) -> str:
        address = str(value or "").strip().lower()
        if not re.fullmatch(r"0x[0-9a-f]{40}", address):
            raise ValueError("wallet_address must be a valid 0x address.")
        return address

    def _format_worker_credit_units(self, value: int) -> str:
        base = 10**18
        whole, fraction = divmod(max(0, int(value)), base)
        if not fraction:
            return str(whole)
        fraction_text = str(fraction).rjust(18, "0").rstrip("0")
        return f"{whole}.{fraction_text}"

    def _worker_base_units_to_hub_credit_wei(self, value: Any) -> int:
        base_units = int(value or 0)
        if base_units <= 0:
            raise ValueError("payment_amount_base_units must be positive.")
        return base_units

    def _worker_wallet_funding_credits_granted_wei(self, *, receipt: dict[str, Any], body: dict[str, Any], payment_amount_base_units: int) -> int:
        raw_value = receipt.get("credits_granted_wei", body.get("credits_granted_wei", None))
        if raw_value is None or str(raw_value).strip() == "":
            return self._worker_base_units_to_hub_credit_wei(payment_amount_base_units)

        credits_wei = int(raw_value or 0)
        if credits_wei <= 0:
            return self._worker_base_units_to_hub_credit_wei(payment_amount_base_units)
        return credits_wei

    def _worker_wallet_funding_current_json_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        raw_roots = [
            getattr(self.server, "debug_root", None),
            getattr(self.server.config, "workspace", None),
            getattr(self.server.config, "hub_root", None),
            Path.cwd(),
        ]
        for raw in raw_roots:
            if raw is None:
                continue
            try:
                base = Path(raw).resolve()
            except Exception:
                continue
            for root in [base, *base.parents]:
                candidates.append(root / "runtime" / "deployments" / "current.json")

        unique: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    def _load_worker_wallet_funding_bridge_config(self) -> dict[str, Any]:
        last_error = ""
        for path in self._worker_wallet_funding_current_json_candidates():
            try:
                if not path.exists():
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    continue
                contracts = payload.get("contracts") if isinstance(payload.get("contracts"), dict) else {}
                escrow = contracts.get("hub_credit_bridge_escrow") if isinstance(contracts.get("hub_credit_bridge_escrow"), dict) else {}
                chain = payload.get("chain") if isinstance(payload.get("chain"), dict) else {}
                source = payload.get("source")
                contract_address = str(escrow.get("address") or "").strip()
                if not re.fullmatch(r"0x[0-9a-fA-F]{40}", contract_address):
                    raise ValueError("contracts.hub_credit_bridge_escrow.address is missing or invalid.")
                controller = str(escrow.get("bridge_controller_address") or "").strip()
                if controller and not re.fullmatch(r"0x[0-9a-fA-F]{40}", controller):
                    raise ValueError("contracts.hub_credit_bridge_escrow.bridge_controller_address is invalid.")
                chain_id = int(escrow.get("chain_id") or chain.get("chain_id") or self.server.config.xlag_chain_id or 0)
                if chain_id <= 0:
                    raise ValueError("deployment chain_id is missing.")
                rpc_url = str(chain.get("rpc_url") or chain.get("host_rpc_url") or self.server.config.energy_chain_rpc_url or "").strip()
                if not rpc_url:
                    raise ValueError("deployment RPC URL is missing.")

                return {
                    "ok": True,
                    "chain_id": chain_id,
                    "chain_id_hex": hex(chain_id),
                    "rpc_url": rpc_url,
                    "hub_credit_bridge_escrow_address": contract_address,
                    "contract_address": contract_address,
                    "bridge_controller_address": controller,
                    "current_json_path": str(path),
                    "source": source,
                    "funding_model": "hub_credit_bridge_escrow_wallet_v2",
                }
            except Exception as exc:
                last_error = f"{path}: {exc}"
                continue
        detail = f" Last error: {last_error}" if last_error else ""
        raise FileNotFoundError("Could not find runtime/deployments/current.json with hub_credit_bridge_escrow metadata." + detail)

    def _handle_worker_wallet_funding_config(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker wallet funding config is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            config = self._load_worker_wallet_funding_bridge_config()
            self.server.signal(
                "api-worker-wallet-funding-config",
                contract_address=config.get("hub_credit_bridge_escrow_address"),
                chain_id=config.get("chain_id"),
            )
            self._send_json(config)
        except Exception as exc:
            self.server.signal("api-worker-wallet-funding-config-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_wallet_balance(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker wallet balance checks are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            wallet_address = self._normalize_worker_wallet_address(body.get("wallet_address"))
            chain_id_hex = str(self.server.energy_chain.rpc("eth_chainId"))
            chain_id = int(chain_id_hex, 16)
            expected_chain_id = self.server.config.xlag_chain_id
            if expected_chain_id is not None and chain_id != expected_chain_id:
                raise RuntimeError(f"Local RPC chain id {chain_id} does not match expected {expected_chain_id}.")
            balance_base_units = int(self.server.energy_chain.get_balance(wallet_address))
            self.server.signal(
                "api-worker-wallet-balance",
                wallet_address=wallet_address,
                chain_id=chain_id,
                available_credits=self._format_worker_credit_units(balance_base_units),
            )
            self._send_json(
                {
                    "ok": True,
                    "wallet_address": wallet_address,
                    "chain_id": chain_id,
                    "chain_id_hex": chain_id_hex,
                    "expected_chain_id": expected_chain_id,
                    "balance_base_units": str(balance_base_units),
                    "available_credits": self._format_worker_credit_units(balance_base_units),
                    "source": "local-rpc",
                }
            )
        except Exception as exc:
            self.server.signal("api-worker-wallet-balance-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _load_worker_multisession_key_cache(self) -> dict[str, Any]:
        path = self._worker_multisession_key_cache_path()
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
        data.setdefault("version", "main-computer-worker-multisession-key-cache-v1")
        return data

    def _save_worker_multisession_key_cache(self, data: dict[str, Any]) -> None:
        path = self._worker_multisession_key_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _normalize_worker_multisession_key_record(
        self,
        key: dict[str, Any],
        *,
        hub_url: str = "",
        fallback_wallet_address: str = "",
    ) -> dict[str, Any]:
        key_id = str(key.get("id") or "").strip()
        if not key_id:
            raise ValueError("multi-session key id is required.")
        wallet_address = self._normalize_worker_wallet_address(key.get("wallet_address") or fallback_wallet_address)
        return {
            "id": key_id,
            "status": str(key.get("status") or "active"),
            "created_at": str(key.get("created_at") or key.get("createdAt") or ""),
            "revoked_at": str(key.get("revoked_at") or key.get("revokedAt") or ""),
            "wallet_address": wallet_address,
            "chain_id": str(key.get("chain_id") or key.get("chainId") or ""),
            "request_id": str(key.get("request_id") or key.get("requestId") or ""),
            "origin": str(key.get("origin") or ""),
            "hub_url": self._clean_hub_url(str(key.get("hub_url") or hub_url), allow_empty=True),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _store_worker_multisession_key_from_hub_result(
        self,
        *,
        hub_url: str,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        key = result.get("key") if isinstance(result.get("key"), dict) else None
        if not key:
            return None
        verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
        fallback_wallet = (
            key.get("wallet_address")
            or verification.get("wallet_address")
            or verification.get("recovered_address")
            or ""
        )
        record = self._normalize_worker_multisession_key_record(
            key,
            hub_url=hub_url,
            fallback_wallet_address=str(fallback_wallet),
        )
        data = self._load_worker_multisession_key_cache()
        data["keys"][record["id"]] = record
        self._save_worker_multisession_key_cache(data)
        return record

    def _worker_multisession_keys_for_wallet(
        self,
        *,
        wallet_address: str,
        hub_url: str = "",
    ) -> list[dict[str, Any]]:
        normalized_wallet = self._normalize_worker_wallet_address(wallet_address)
        normalized_hub_url = self._clean_hub_url(hub_url, allow_empty=True)
        data = self._load_worker_multisession_key_cache()
        records: list[dict[str, Any]] = []
        for value in data.get("keys", {}).values():
            if not isinstance(value, dict):
                continue
            try:
                record = self._normalize_worker_multisession_key_record(
                    value,
                    hub_url=str(value.get("hub_url") or ""),
                    fallback_wallet_address=str(value.get("wallet_address") or ""),
                )
            except ValueError:
                continue
            if record["wallet_address"] != normalized_wallet:
                continue
            if normalized_hub_url and record.get("hub_url") and record["hub_url"] != normalized_hub_url:
                continue
            records.append(record)
        records.sort(key=lambda item: (item.get("status") != "active", item.get("created_at", ""), item.get("id", "")), reverse=False)
        return records

    def _handle_worker_multisession_keys_load(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker multi-session key load is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            wallet_address = self._normalize_worker_wallet_address(body.get("wallet_address"))
            hub_url = self._clean_hub_url(str(body.get("hub_url") or ""), allow_empty=True)
            keys = self._worker_multisession_keys_for_wallet(wallet_address=wallet_address, hub_url=hub_url)
            active_key = next((key for key in keys if key.get("status") == "active"), None)
            self.server.signal(
                "api-worker-multisession-keys-load",
                hub_url=hub_url,
                wallet_address=wallet_address,
                key_count=len(keys),
                active_key_id=(active_key or {}).get("id", ""),
            )
            self._send_json(
                {
                    "ok": True,
                    "wallet_address": wallet_address,
                    "hub_url": hub_url,
                    "keys": keys,
                    "active_key": active_key,
                }
            )
        except Exception as exc:
            self.server.signal("api-worker-multisession-keys-load-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_wallet_funding_balance(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker wallet funding balance checks are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            wallet_address = self._normalize_worker_wallet_address(body.get("wallet_address"))
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            balance = self._fetch_worker_wallet_funding_balance_from_hub(hub_url=hub_url, wallet_address=wallet_address)
            self.server.signal(
                "api-worker-wallet-funding-balance",
                hub_url=hub_url,
                wallet_address=wallet_address,
                available_credits=(balance.get("account") or {}).get("available_credits", 0) if isinstance(balance.get("account"), dict) else 0,
            )
            self._send_json({"ok": True, "hub_url": hub_url, **balance})
        except Exception as exc:
            self.server.signal("api-worker-wallet-funding-balance-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_wallet_funding_complete(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker wallet funding completion is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            wallet_address = self._normalize_worker_wallet_address(body.get("wallet_address"))
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            receipt = body.get("deposit_receipt")
            if not isinstance(receipt, dict):
                receipt = body
            deposit_id = str(receipt.get("deposit_id", body.get("deposit_id", ""))).strip().lower()
            forwarded = {
                "wallet_address": wallet_address,
                "deposit_id": deposit_id,
                "chain_id": int(receipt.get("chain_id", body.get("chain_id", 0)) or 0),
                "contract_address": str(receipt.get("contract_address", body.get("contract_address", ""))),
                "tx_hash": str(receipt.get("tx_hash", receipt.get("transaction_hash", body.get("tx_hash", "")))),
            }
            result = self._post_worker_wallet_funding_completion_to_hub(hub_url=hub_url, payload=forwarded)
            self.server.signal(
                "api-worker-wallet-funding-complete",
                hub_url=hub_url,
                wallet_address=wallet_address,
                deposit_id=deposit_id,
                tx_hash=forwarded["tx_hash"],
                idempotent=bool(result.get("idempotent")),
            )
            self._send_json({"ok": True, "hub_url": hub_url, **result})
        except Exception as exc:
            self.server.signal("api-worker-wallet-funding-complete-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_wallet_funding_import(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker wallet funding imports are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            wallet_address = self._normalize_worker_wallet_address(body.get("wallet_address"))
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            receipt = body.get("deposit_receipt")
            if not isinstance(receipt, dict):
                receipt = body

            payment_amount_base_units = int(receipt.get("payment_amount_base_units", body.get("payment_amount_base_units", 0)) or 0)
            forwarded = {
                "wallet_address": wallet_address,
                "account_id": wallet_address,
                "chain_id": int(receipt.get("chain_id", body.get("chain_id", 0)) or 0),
                "contract_address": str(receipt.get("contract_address", body.get("contract_address", ""))),
                "tx_hash": str(receipt.get("tx_hash", receipt.get("transaction_hash", body.get("tx_hash", "")))),
                "log_index": int(receipt.get("log_index", body.get("log_index", 0)) or 0),
                "block_number": int(receipt.get("block_number", body.get("block_number", 0)) or 0),
                "payer_address": str(receipt.get("payer_address", body.get("payer_address", wallet_address)) or wallet_address),
                "payment_asset": str(receipt.get("payment_asset", body.get("payment_asset", "native")) or "native"),
                "payment_amount_base_units": payment_amount_base_units,
                "credits_granted_wei": self._worker_wallet_funding_credits_granted_wei(
                    receipt=receipt,
                    body=body,
                    payment_amount_base_units=payment_amount_base_units,
                ),
                "memo": str(receipt.get("memo", body.get("memo", "Worker wallet bridge funding import"))),
            }
            result = self._post_worker_wallet_funding_import_to_hub(hub_url=hub_url, payload=forwarded)
            self.server.signal(
                "api-worker-wallet-funding-import",
                hub_url=hub_url,
                wallet_address=wallet_address,
                tx_hash=forwarded["tx_hash"],
                idempotent=bool(result.get("idempotent")),
            )
            self._send_json({"ok": True, "hub_url": hub_url, **result})
        except Exception as exc:
            self.server.signal("api-worker-wallet-funding-import-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_multisession_key_request(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker multi-session key requests are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            signed_request = body.get("signed_request")
            if not isinstance(signed_request, dict):
                raise ValueError("signed_request object is required.")
            if signed_request.get("kind") != "main_computer_multisession_key_request":
                raise ValueError("signed_request.kind must be main_computer_multisession_key_request.")
            if signed_request.get("signing_method") != "personal_sign":
                raise ValueError("signed_request.signing_method must be personal_sign.")
            if not str(signed_request.get("signature") or "").startswith("0x"):
                raise ValueError("signed_request.signature is required.")

            forwarded = {
                "signed_request": signed_request,
                "client_metadata": dict(body.get("client_metadata", {})) if isinstance(body.get("client_metadata"), dict) else {},
            }
            result = self._post_multisession_key_request_to_hub(hub_url=hub_url, payload=forwarded)
            key = result.get("key") if isinstance(result.get("key"), dict) else {}
            local_record = self._store_worker_multisession_key_from_hub_result(hub_url=hub_url, result=result)
            self.server.signal(
                "api-worker-multisession-key-request",
                hub_url=hub_url,
                key_id=key.get("id", ""),
                local_cached=bool(local_record),
            )
            response = {"ok": True, "hub_url": hub_url, **result}
            if local_record:
                response["local_cache"] = {"stored": True, "key": local_record}
            self._send_json(response)
        except Exception as exc:
            self.server.signal("api-worker-multisession-key-request-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _worker_registration_payload_from_ui(self, worker_payload: dict[str, Any]) -> dict[str, Any]:
        models = [str(item).strip() for item in worker_payload.get("models", []) if str(item).strip()] if isinstance(worker_payload.get("models"), list) else []
        model = str(worker_payload.get("model") or (models[0] if models else "")).strip()
        if model and model not in models:
            models.insert(0, model)
        if not models:
            raise ValueError("At least one worker model is required.")

        pricing = dict(worker_payload.get("pricing", {})) if isinstance(worker_payload.get("pricing"), dict) else {}
        try:
            credits_per_request = int(pricing.get("credits_per_request", worker_payload.get("credits_per_request", 0)) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("credits_per_request must be a positive integer.") from exc
        if credits_per_request <= 0:
            raise ValueError("credits_per_request must be a positive integer.")

        execution = dict(worker_payload.get("execution", {})) if isinstance(worker_payload.get("execution"), dict) else {}
        execution_mode = str(execution.get("mode") or worker_payload.get("execution_mode") or "worker_pull_v0").strip() or "worker_pull_v0"
        max_concurrency = max(1, int(execution.get("max_concurrency", worker_payload.get("max_concurrency", 1)) or 1))
        capabilities = dict(worker_payload.get("capabilities", {})) if isinstance(worker_payload.get("capabilities"), dict) else {}
        capabilities.setdefault("capabilities", ["chat.completions"])
        capabilities["pricing"] = {
            "pricing_type": str(pricing.get("pricing_type") or "fixed_per_call_v0"),
            "credits_per_request": credits_per_request,
            "unit": str(pricing.get("unit") or "compute_credit"),
        }
        capabilities["execution"] = {
            "mode": execution_mode,
            "max_concurrency": max_concurrency,
        }
        capabilities["phase12_worker_seller_offer_ui"] = True

        payload = {
            "node_id": str(worker_payload.get("node_id") or "").strip(),
            "endpoint": self._clean_hub_url(str(worker_payload.get("endpoint") or "")),
            "model": model,
            "models": models,
            "credits_per_request": credits_per_request,
            "max_concurrency": max_concurrency,
            "queue_depth": max(0, int(worker_payload.get("queue_depth", 0) or 0)),
            "active_requests": max(0, int(worker_payload.get("active_requests", 0) or 0)),
            "pricing": capabilities["pricing"],
            "execution": capabilities["execution"],
            "capabilities": capabilities,
        }
        if not payload["node_id"]:
            raise ValueError("worker node_id is required.")
        return payload

    def _handle_worker_offer_register(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker offer registration is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            worker_payload = body.get("worker")
            if not isinstance(worker_payload, dict):
                raise ValueError("worker registration payload is required.")

            payload = self._worker_registration_payload_from_ui(worker_payload)
            registration = self._post_worker_registration_to_hub(hub_url=hub_url, payload=payload)
            worker = registration.get("worker") if isinstance(registration.get("worker"), dict) else {}
            offer = worker.get("offer") if isinstance(worker.get("offer"), dict) else {}
            self.server.signal(
                "api-worker-offer-register",
                hub_url=hub_url,
                node_id=payload["node_id"],
                offer_id=offer.get("offer_id", ""),
            )
            self._send_json(
                {
                    "ok": True,
                    "hub_url": hub_url,
                    "registration": registration,
                    "worker": worker,
                    "offer": offer,
                }
            )
        except Exception as exc:
            self.server.signal("api-worker-offer-register-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _hub_config_payload(self) -> dict[str, Any]:
        saved = self._load_hub_config()
        hub_url = self._clean_hub_url(str(saved.get("hub_url") or self.server.config.hub_url))
        return {
            "ok": True,
            "provider": self.server.config.provider,
            "active_provider": self.server.provider_name,
            "model": self.server.config.model,
            "hub_url": self.server.config.hub_url,
            "hub_client_node_id": self.server.config.hub_client_node_id,
            "hub_high_security": self.server.config.hub_high_security,
            "hub_timeout_s": self.server.config.hub_timeout_s,
            "is_hub_provider": self.server.config.provider == "hub",
            "saved": saved,
            "local_hub_status": self._fetch_hub_status(hub_url),
        }

    def _load_hub_config(self) -> dict[str, Any]:
        path = self.server.debug_root / "hub_configuration.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        try:
            hub_timeout_s = float(data.get("hub_timeout_s", self.server.config.hub_timeout_s) or self.server.config.hub_timeout_s)
        except (TypeError, ValueError):
            hub_timeout_s = self.server.config.hub_timeout_s
        return {
            "hub_url": str(data.get("hub_url") or self.server.config.hub_url),
            "hub_client_node_id": str(data.get("hub_client_node_id") or self.server.config.hub_client_node_id),
            "hub_high_security": self._coerce_bool(data.get("hub_high_security"), default=self.server.config.hub_high_security),
            "hub_timeout_s": hub_timeout_s,
            "upstream_hub_url": str(data.get("upstream_hub_url") or ""),
        }

    def _save_hub_config(self, data: dict[str, Any]) -> dict[str, Any]:
        path = self.server.debug_root / "hub_configuration.json"
        existing = self._load_hub_config()
        saved = {**existing, **data}
        saved.pop("provider", None)
        saved["hub_url"] = self._clean_hub_url(str(saved.get("hub_url") or self.server.config.hub_url))
        saved["upstream_hub_url"] = self._clean_hub_url(str(saved.get("upstream_hub_url") or ""), allow_empty=True)
        path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        return saved

    def _clean_hub_url(self, value: str, *, allow_empty: bool = False) -> str:
        clean = str(value or "").strip().rstrip("/")
        if allow_empty and not clean:
            return ""
        if not clean:
            raise ValueError("Hub URL is required.")
        if not clean.startswith(("http://", "https://")):
            raise ValueError("Hub URL must start with http:// or https://.")
        return clean

    def _hub_json_request_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": "MainComputerWorker/0.1",
            "Accept": "application/json",
        }
        if extra:
            headers.update({str(key): str(value) for key, value in extra.items()})
        return headers

    def _fetch_hub_status(self, hub_url: str) -> dict[str, Any]:
        request = Request(
            self._clean_hub_url(hub_url) + "/api/hub/status",
            headers=self._hub_json_request_headers(),
        )
        try:
            with urlopen(request, timeout=2.0) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Hub returned a non-object response.")
            return {"reachable": True, "status": data}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace").strip()
            detail = body or exc.reason or "Forbidden"
            return {"reachable": False, "http_status": exc.code, "error": f"Hub returned HTTP {exc.code}: {detail}"}
        except Exception as exc:
            return {"reachable": False, "error": str(exc)}

    def _register_upstream_hub(
        self,
        *,
        local_hub_url: str,
        upstream_hub_url: str,
        node_id: str,
        credits_per_request: int,
    ) -> dict[str, Any]:
        payload = {
            "node_id": node_id,
            "endpoint": self._clean_hub_url(upstream_hub_url),
            "credits_per_request": max(1, int(credits_per_request or 1)),
        }
        request = Request(
            self._clean_hub_url(local_hub_url) + "/api/hub/upstreams/register",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Local hub returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Local hub is unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Local hub returned a non-object upstream registration response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def _post_multisession_key_request_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self._clean_hub_url(hub_url) + "/api/hub/v1/credits/multisession-keys/request",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub is unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Hub returned a non-object multi-session key response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def _fetch_worker_wallet_funding_balance_from_hub(self, *, hub_url: str, wallet_address: str) -> dict[str, Any]:
        query = urlencode({"wallet_address": wallet_address})
        request = Request(
            self._clean_hub_url(hub_url) + f"/api/hub/v1/credits/balance?{query}",
            headers=self._hub_json_request_headers(),
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub is unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Hub returned a non-object wallet funding balance response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def _post_worker_wallet_funding_completion_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Ask Hub to complete a bridge escrow deposit by deposit id.

        The browser wallet has already submitted depositFor(...).  The viewport
        forwards only the deposit id and wallet identity; the Hub must verify the
        amount on-chain before crediting the ledger.
        """

        hub_base = self._clean_hub_url(hub_url)
        wallet_address = str(payload.get("wallet_address", "")).strip().lower()
        deposit_id = str(payload.get("deposit_id", "")).strip().lower()
        if not re.fullmatch(r"0x[0-9a-f]{64}", deposit_id):
            raise ValueError("deposit_id must be a 32-byte 0x-prefixed hex value.")

        route_payload = {
            "deposit_id": deposit_id,
            "wallet_address": wallet_address,
        }
        if payload.get("tx_hash"):
            route_payload["tx_hash"] = str(payload.get("tx_hash", "")).strip()
        if payload.get("contract_address"):
            route_payload["contract_address"] = str(payload.get("contract_address", "")).strip()
        if payload.get("chain_id") is not None:
            route_payload["chain_id"] = int(payload.get("chain_id") or 0)
        encoded = json.dumps(route_payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            hub_base + "/api/hub/v1/credits/wallet-funding/complete",
            data=encoded,
            headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urlopen(request, timeout=30.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub returned HTTP {exc.code} from /api/hub/v1/credits/wallet-funding/complete: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub is unreachable: {exc}") from exc

        if not isinstance(data, dict):
            raise RuntimeError("Hub returned a non-object wallet funding completion response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        data.setdefault("wallet_address", wallet_address)
        data.setdefault("account_id", wallet_address)
        data.setdefault("funding_model", "hub_credit_bridge_escrow_wallet_v2")
        data["wallet_funding_completion_endpoint"] = "/api/hub/v1/credits/wallet-funding/complete"
        return data

    def _post_worker_wallet_funding_import_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Forward a confirmed wallet-funding receipt to Hub.

        The on-chain transaction has already happened before this helper runs, so a
        missing newest Hub route must not force the user to submit another funding
        transaction.  New Hubs expose the wallet-specific import route; older Hubs
        still expose the normalized deposit/purchase import aliases that record the
        same chain receipt idempotently for the wallet address.
        """

        hub_base = self._clean_hub_url(hub_url)
        import_paths = [
            ("/api/hub/v1/credits/wallet-funding/import", "wallet-funding"),
            ("/api/hub/v1/credits/deposits/import", "legacy-deposit-import"),
            ("/api/hub/v1/credits/purchases/import", "legacy-purchase-import"),
        ]
        route_errors: list[str] = []

        for index, (path, mode) in enumerate(import_paths):
            route_payload = dict(payload)
            wallet_address = str(route_payload.get("wallet_address", "")).strip().lower()
            if wallet_address:
                route_payload["wallet_address"] = wallet_address
                route_payload.setdefault("account_id", wallet_address)
            encoded = json.dumps(route_payload, ensure_ascii=False).encode("utf-8")
            request = Request(
                hub_base + path,
                data=encoded,
                headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
                method="POST",
            )
            try:
                with urlopen(request, timeout=10.0) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                route_errors.append(f"{path} -> HTTP {exc.code}: {body}")
                if exc.code in {HTTPStatus.NOT_FOUND, HTTPStatus.METHOD_NOT_ALLOWED} and index + 1 < len(import_paths):
                    continue
                raise RuntimeError(f"Hub returned HTTP {exc.code} from {path}: {body}") from exc
            except URLError as exc:
                raise RuntimeError(f"Hub is unreachable: {exc}") from exc

            if not isinstance(data, dict):
                raise RuntimeError(f"Hub returned a non-object wallet funding import response from {path}.")
            if data.get("error"):
                raise RuntimeError(str(data["error"]))

            if mode != "wallet-funding":
                data = dict(data)
                wallet_address = str(payload.get("wallet_address", ""))
                if wallet_address:
                    data.setdefault("wallet_address", wallet_address)
                    data.setdefault("account_id", wallet_address.lower())
                data.setdefault("funding_model", "hub_credit_bridge_escrow_wallet_v1")
                data["wallet_funding_import_endpoint"] = path
                data["wallet_funding_import_fallback"] = True
                if route_errors:
                    data["wallet_funding_import_route_errors"] = route_errors
            return data

        raise RuntimeError("Hub wallet funding import failed: " + " ; ".join(route_errors))

    def _post_worker_connect_order_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self._clean_hub_url(hub_url) + "/api/hub/v1/workers/connect",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub is unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Hub returned a non-object worker connect response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def _post_worker_registration_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self._clean_hub_url(hub_url) + "/api/hub/v1/workers/register",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub is unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Hub returned a non-object worker registration response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def _energy_passcode_ok(self, body: dict[str, Any]) -> bool:
        required = self.server.config.energy_admin_passcode
        if not required:
            return True
        supplied = str(body.get("passcode") or self.headers.get("X-Main-Computer-Energy-Passcode") or "")
        return supplied == required
