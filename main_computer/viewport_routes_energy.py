from __future__ import annotations

from dataclasses import replace
import ipaddress
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from main_computer.viewport_state import *  # noqa: F401,F403

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

            bridge_context = body.get("bridge_context")
            if bridge_context is None:
                bridge_context = {}
            if not isinstance(bridge_context, dict):
                raise ValueError("bridge_context must be an object when supplied.")

            forwarded = {
                "signed_request": signed_request,
                "bridge_context": bridge_context,
                "client_metadata": dict(body.get("client_metadata", {})) if isinstance(body.get("client_metadata"), dict) else {},
            }
            result = self._post_multisession_key_request_to_hub(hub_url=hub_url, payload=forwarded)
            key = result.get("key") if isinstance(result.get("key"), dict) else {}
            self.server.signal(
                "api-worker-multisession-key-request",
                hub_url=hub_url,
                key_id=key.get("id", ""),
            )
            self._send_json({"ok": True, "hub_url": hub_url, **result})
        except Exception as exc:
            self.server.signal("api-worker-multisession-key-request-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

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

    def _fetch_hub_status(self, hub_url: str) -> dict[str, Any]:
        try:
            with urlopen(self._clean_hub_url(hub_url) + "/api/hub/status", timeout=2.0) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Hub returned a non-object response.")
            return {"reachable": True, "status": data}
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
            headers={"Content-Type": "application/json"},
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
            headers={"Content-Type": "application/json"},
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

    def _post_worker_registration_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self._clean_hub_url(hub_url) + "/api/hub/v1/workers/register",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
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
