from __future__ import annotations

from dataclasses import replace
import ipaddress
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
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

            # Keep the normalized wallet address as the hub account id for legacy
            # deposit/purchase import routes.  Newer hubs derive this in the
            # wallet-funding route, but older hubs reject otherwise-valid receipts
            # with "account_id is required" before they look at wallet_address.
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
                "payment_amount_base_units": int(receipt.get("payment_amount_base_units", body.get("payment_amount_base_units", 0)) or 0),
                "credits_granted": int(receipt.get("credits_granted", body.get("credits_granted", 0)) or 0),
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

    def _fetch_worker_wallet_funding_balance_from_hub(self, *, hub_url: str, wallet_address: str) -> dict[str, Any]:
        query = urlencode({"wallet_address": wallet_address})
        try:
            with urlopen(self._clean_hub_url(hub_url) + f"/api/hub/v1/credits/balance?{query}", timeout=5.0) as response:
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

    def _post_worker_wallet_funding_import_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Forward a confirmed wallet-funding receipt to Hub.

        The on-chain transaction has already happened before this helper runs, so a
        missing newest Hub route must not force the user to submit another funding
        transaction.  New Hubs expose the wallet-specific import route; older Hubs
        still expose the normalized deposit/purchase import aliases that record the
        same chain receipt idempotently for the wallet address.
        """

        hub_base = self._clean_hub_url(hub_url)
        wallet_address = str(payload.get("wallet_address", "")).strip().lower()
        account_id = str(payload.get("account_id", "") or wallet_address).strip().lower()
        import_paths = [
            ("/api/hub/v1/credits/wallet-funding/import", "wallet-funding"),
            ("/api/hub/v1/credits/deposits/import", "legacy-deposit-import"),
            ("/api/hub/v1/credits/purchases/import", "legacy-purchase-import"),
        ]
        route_errors: list[str] = []

        for index, (path, mode) in enumerate(import_paths):
            route_payload = dict(payload)
            if mode != "wallet-funding":
                if wallet_address:
                    route_payload.setdefault("wallet_address", wallet_address)
                if account_id:
                    route_payload.setdefault("account_id", account_id)
            encoded = json.dumps(route_payload, ensure_ascii=False).encode("utf-8")
            request = Request(
                hub_base + path,
                data=encoded,
                headers={"Content-Type": "application/json"},
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
                if wallet_address:
                    data.setdefault("wallet_address", wallet_address)
                if account_id:
                    data.setdefault("account_id", account_id)
                data.setdefault("funding_model", "hub_credit_bridge_escrow_wallet_v1")
                data["wallet_funding_import_endpoint"] = path
                data["wallet_funding_import_fallback"] = True
                if route_errors:
                    data["wallet_funding_import_route_errors"] = route_errors
            return data

        raise RuntimeError("Hub wallet funding import failed: " + " ; ".join(route_errors))

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
