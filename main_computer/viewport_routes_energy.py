from __future__ import annotations

from dataclasses import replace
import ipaddress
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from main_computer.viewport_state import *  # noqa: F401,F403
from main_computer.hub_networks import HubNetworkConfigError, load_hub_network_registry
from main_computer.windows_user_activity import collect_windows_user_activity
from main_computer.credit_units import credit_decimal_text_to_wei, credit_wei_to_decimal_text

class ViewportEnergyRoutesMixin:

    _ENERGY_RPC_USER_AGENT = "MainComputerEnergy/1.0"
    _ENERGY_NETWORK_ORDER = ("mainnet", "testnet", "test", "dev")
    _WORKER_DEFAULT_CREDITS_PER_REQUEST = "1.024"
    _WORKER_DEFAULT_CREDITS_PER_TOKEN = "0.001"
    _WORKER_DEFAULT_SELLER_TARGET_TOKENS = 1024
    _WORKER_DEFAULT_SELLER_MODEL = "gemma4:26b"
    _WORKER_SELLER_AVAILABILITY_TOTAL_IDLE = "totally_idle"
    _WORKER_SELLER_AVAILABILITY_AI_IDLE = "ai_idle"
    _WORKER_SELLER_AVAILABILITY_MODES = {_WORKER_SELLER_AVAILABILITY_TOTAL_IDLE, _WORKER_SELLER_AVAILABILITY_AI_IDLE}
    _WORKER_LEGACY_CREDITS_PER_REQUESTS = {"5500123", "5500123.0", "5500123.00", "1.25", "1.250", "1.2500"}
    _WORKER_LEGACY_SELLER_MODELS = {"mock-ai-model-phase9"}
    _ENERGY_EXPECTED_CONTRACTS = (
        ("alpha-beta-lockout", "AlphaBetaLockout"),
        ("xlag-bridge-reserve", "XLagBridgeReserve"),
        ("hub_credit_bridge_escrow", "HubCreditBridgeEscrow"),
    )
    _ANVIL_DEFAULT_OFFICES = {
        "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
        "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
        "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
        "0x90f79bf6eb2c4f870365e785982e1f101e93b906",
    }

    def _energy_bool_query(self, name: str, *, default: bool = True) -> bool:
        query = parse_qs(urlsplit(self.path).query)
        raw = query.get(name, [None])[0]
        if raw is None:
            return default
        return str(raw).strip().lower() not in {"0", "false", "no", "off"}

    def _energy_hex_to_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(str(value), 16 if str(value).strip().lower().startswith("0x") else 10)
        except (TypeError, ValueError):
            return None

    def _energy_rpc_call(self, rpc_url: str, method: str, params: list[Any] | None = None, *, timeout_s: float = 0.75) -> Any:
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8")
        request = Request(
            str(rpc_url),
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": self._ENERGY_RPC_USER_AGENT,
            },
            method="POST",
        )
        with urlopen(request, timeout=timeout_s) as response:
            result = json.loads(response.read().decode("utf-8"))
        if isinstance(result, dict) and result.get("error"):
            error = result.get("error")
            if isinstance(error, dict):
                raise ValueError(str(error.get("message") or error))
            raise ValueError(str(error))
        if not isinstance(result, dict):
            raise ValueError("RPC response was not a JSON object.")
        return result.get("result")

    def _energy_profile_manifest_path(self, profile: Any) -> Path:
        path = profile.deployment_manifest_path or Path("runtime") / "deployments" / profile.network_key / "latest.json"
        path = Path(path)
        if path.is_absolute():
            return path
        return self.server.debug_root / path

    def _energy_load_manifest_status(self, profile: Any) -> tuple[Path, dict[str, Any] | None, list[str]]:
        path = self._energy_profile_manifest_path(profile)
        warnings: list[str] = []
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return path, None, [f"Deployment manifest is missing: {path}"]
        except json.JSONDecodeError as exc:
            return path, None, [f"Deployment manifest is not valid JSON: {exc}"]
        if not isinstance(manifest, dict):
            return path, None, ["Deployment manifest root is not an object."]
        return path, manifest, warnings

    def _energy_contract_map(self, manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        if not isinstance(manifest, dict):
            return {}
        raw = manifest.get("contracts")
        if not isinstance(raw, dict):
            raw = manifest.get("deployments")
        if not isinstance(raw, dict):
            return {}
        contracts: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            if isinstance(value, dict):
                contracts[str(key)] = value
        return contracts

    def _energy_contract_inventory(
        self,
        *,
        contracts: dict[str, dict[str, Any]],
        rpc_url: str,
        live: bool,
        rpc_reachable: bool,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        expected_keys = [key for key, _label in self._ENERGY_EXPECTED_CONTRACTS]
        extra_keys = sorted(key for key in contracts if key not in expected_keys)
        inventory: list[dict[str, Any]] = []
        for key, label in [*self._ENERGY_EXPECTED_CONTRACTS, *[(extra, extra) for extra in extra_keys]]:
            raw = contracts.get(key) or {}
            address = str(raw.get("address") or "").strip()
            code_bytes = None
            has_code = None
            code_error = ""
            if key in expected_keys and not raw:
                warnings.append(f"{label} is missing from the deployment manifest.")
            elif not address:
                warnings.append(f"{label} has no deployed address in the manifest.")
            if live and rpc_reachable and address and rpc_url:
                try:
                    code = self._energy_rpc_call(rpc_url, "eth_getCode", [address, "latest"])
                    code_text = str(code or "0x")
                    has_code = code_text not in {"", "0x", "0X"}
                    code_bytes = max(0, (len(code_text.removeprefix("0x").removeprefix("0X")) // 2))
                    if not has_code:
                        warnings.append(f"{label} has no bytecode at {address}.")
                except Exception as exc:
                    code_error = str(exc)
                    warnings.append(f"{label} bytecode check failed: {code_error}")
            inventory.append(
                {
                    "key": key,
                    "label": label,
                    "address": address,
                    "target": str(raw.get("target") or ""),
                    "transaction_hash": str(raw.get("transaction_hash") or ""),
                    "configured": bool(raw),
                    "has_code": has_code,
                    "code_bytes": code_bytes,
                    "code_error": code_error,
                }
            )
        return inventory, warnings

    def _energy_authority_summary(self, manifest: dict[str, Any] | None, *, network_key: str, kind: str) -> tuple[list[dict[str, Any]], list[str], bool]:
        raw_offices = manifest.get("offices") if isinstance(manifest, dict) else None
        offices: list[dict[str, Any]] = []
        warnings: list[str] = []
        default_offices: list[str] = []
        if isinstance(raw_offices, list):
            for office in raw_offices:
                if not isinstance(office, dict):
                    continue
                address = str(office.get("address") or "").strip()
                normalized = address.lower()
                is_default = normalized in self._ANVIL_DEFAULT_OFFICES
                if is_default:
                    default_offices.append(str(office.get("title") or office.get("office") or address))
                offices.append(
                    {
                        "office": str(office.get("office") or ""),
                        "title": str(office.get("title") or ""),
                        "address": address,
                        "default_anvil": is_default,
                    }
                )
        elif manifest is not None:
            warnings.append("Deployment manifest does not include office authority.")
        if default_offices and str(kind).lower() in {"mainnet", "testnet"}:
            warnings.append(
                f"{network_key} authority is unsafe: {', '.join(default_offices)} match default Anvil office identities."
            )
        elif default_offices and str(kind).lower() == "test":
            warnings.append(
                f"{network_key} is using default Anvil office identities for local validation."
            )
        return offices, warnings, bool(default_offices and str(kind).lower() in {"mainnet", "testnet"})

    def _energy_network_status(self, profile: Any, *, live: bool) -> dict[str, Any]:
        manifest_path, manifest, warnings = self._energy_load_manifest_status(profile)
        manifest_chain = manifest.get("chain") if isinstance(manifest, dict) else {}
        if not isinstance(manifest_chain, dict):
            manifest_chain = {}
        manifest_environment = str(manifest.get("environment") or "") if isinstance(manifest, dict) else ""
        manifest_chain_id = self._energy_hex_to_int(manifest_chain.get("chain_id")) if manifest_chain else None
        run_id = str(manifest.get("run_id") or "") if isinstance(manifest, dict) else ""
        created_at = str(manifest.get("created_at") or "") if isinstance(manifest, dict) else ""
        source = manifest.get("source") if isinstance(manifest, dict) else {}
        if not isinstance(source, dict):
            source = {}
        source_kind = str(source.get("kind") or source.get("source_kind") or "")
        rpc_url = str(profile.chain_rpc_url or manifest_chain.get("host_rpc_url") or manifest_chain.get("rpc_url") or "").strip()
        expected_chain_id = profile.chain_id

        if manifest is not None and manifest_environment and manifest_environment != profile.network_key:
            warnings.append(f"Manifest environment {manifest_environment!r} does not match registry network {profile.network_key!r}.")
        if expected_chain_id is not None and manifest_chain_id is not None and int(expected_chain_id) != int(manifest_chain_id):
            warnings.append(f"Manifest chain id {manifest_chain_id} does not match expected chain id {expected_chain_id}.")

        live_chain_id = None
        block_number = None
        rpc_reachable = False
        rpc_error = ""
        if live and rpc_url:
            try:
                live_chain_id = self._energy_hex_to_int(self._energy_rpc_call(rpc_url, "eth_chainId"))
                block_number = self._energy_hex_to_int(self._energy_rpc_call(rpc_url, "eth_blockNumber"))
                rpc_reachable = True
                if expected_chain_id is not None and live_chain_id is not None and int(live_chain_id) != int(expected_chain_id):
                    warnings.append(f"Live RPC chain id {live_chain_id} does not match expected chain id {expected_chain_id}.")
            except Exception as exc:
                rpc_error = str(exc)
                warnings.append(f"RPC unreachable: {rpc_error}")
        elif live and not rpc_url:
            warnings.append("No RPC URL is configured for this network.")

        contracts, contract_warnings = self._energy_contract_inventory(
            contracts=self._energy_contract_map(manifest),
            rpc_url=rpc_url,
            live=live,
            rpc_reachable=rpc_reachable,
        )
        warnings.extend(contract_warnings)
        offices, authority_warnings, unsafe_authority = self._energy_authority_summary(
            manifest,
            network_key=profile.network_key,
            kind=profile.kind,
        )
        warnings.extend(authority_warnings)

        missing_manifest = manifest is None
        chain_mismatch = any("chain id" in warning and "does not match" in warning for warning in warnings)
        contract_missing = any(
            contract["key"] in {key for key, _label in self._ENERGY_EXPECTED_CONTRACTS}
            and (not contract["configured"] or contract.get("has_code") is False)
            for contract in contracts
        )
        if unsafe_authority or chain_mismatch:
            overall = "unsafe"
        elif missing_manifest or (live and not rpc_reachable) or contract_missing or warnings:
            overall = "degraded"
        else:
            overall = "healthy"

        return {
            "network": profile.network_key,
            "network_key": profile.network_key,
            "display_name": profile.display_name,
            "kind": profile.kind,
            "rank": "primary" if profile.network_key == "mainnet" else "secondary",
            "expected_chain_id": expected_chain_id,
            "configured_rpc_url": rpc_url,
            "hub_url": profile.hub_url,
            "deployment_manifest_path": str(manifest_path),
            "manifest_present": manifest is not None,
            "manifest_environment": manifest_environment,
            "manifest_chain_id": manifest_chain_id,
            "run_id": run_id,
            "created_at": created_at,
            "source_kind": source_kind,
            "rpc_reachable": rpc_reachable,
            "rpc_error": rpc_error,
            "live_chain_id": live_chain_id,
            "chain_id_ok": (
                expected_chain_id is not None
                and live_chain_id is not None
                and int(expected_chain_id) == int(live_chain_id)
            )
            if live
            else None,
            "block_number": block_number,
            "contracts": contracts,
            "offices": offices,
            "warnings": warnings,
            "overall_status": overall,
            "read_only": True,
            "mutation_policy": "monitor-only",
        }

    def _handle_energy_networks_status(self) -> None:
        try:
            live = self._energy_bool_query("live", default=True)
            registry = load_hub_network_registry()
            ordered = [key for key in self._ENERGY_NETWORK_ORDER if key in registry.networks]
            ordered.extend(key for key in registry.networks if key not in ordered)
            networks = [self._energy_network_status(registry.networks[key], live=live) for key in ordered]
            selected = registry.default_network if registry.default_network in registry.networks else ordered[0]
            self.server.signal("api-energy-networks-status", live=live, selected=selected, networks=len(networks))
            self._send_json(
                {
                    "ok": True,
                    "schema": "main-computer.energy-networks.status.v1",
                    "mode": "read-only-monitor",
                    "default_network": selected,
                    "live": live,
                    "networks": networks,
                    "summary": {
                        "total": len(networks),
                        "healthy": sum(1 for network in networks if network["overall_status"] == "healthy"),
                        "degraded": sum(1 for network in networks if network["overall_status"] == "degraded"),
                        "unsafe": sum(1 for network in networks if network["overall_status"] == "unsafe"),
                    },
                }
            )
        except Exception as exc:
            self.server.signal("api-energy-networks-status-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

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

    def _worker_credit_amount_text(self, value: Any, default: str | None = None) -> str:
        default_text = str(default or self._WORKER_DEFAULT_CREDITS_PER_REQUEST)
        amount_wei = credit_decimal_text_to_wei(value, default=default_text, minimum_wei=1)
        return credit_wei_to_decimal_text(amount_wei)

    def _worker_credit_amount_wei_text(self, value: Any, default: str | None = None, *, value_is_wei: bool = False) -> str:
        if value_is_wei:
            try:
                parsed = int(str(value).strip())
                if parsed > 0:
                    return str(parsed)
            except (TypeError, ValueError):
                pass
        default_text = str(default or self._WORKER_DEFAULT_CREDITS_PER_REQUEST)
        return str(credit_decimal_text_to_wei(value, default=default_text, minimum_wei=1))

    def _worker_legacy_credit_amount_ceiling_text(self, value_wei: Any, default: str | None = None) -> str:
        """Return an integer credit string for legacy Hub fields.

        Older Hubs parse ``credits_per_request`` with ``int(...)`` even when the
        newer pricing payload also carries precise wei and fractional display
        values.  Keep the precise values in pricing fields, but make the legacy
        top-level field conservative and integer-compatible.
        """

        try:
            amount_wei = int(str(value_wei).strip())
        except (TypeError, ValueError):
            default_text = str(default or self._WORKER_DEFAULT_CREDITS_PER_REQUEST)
            amount_wei = credit_decimal_text_to_wei(default_text, default=default_text, minimum_wei=1)
        credit_base = 10**18
        return str(max(1, (max(1, amount_wei) + credit_base - 1) // credit_base))

    def _worker_seller_credit_amount_text(self, value: Any, default: str | None = None) -> str:
        raw_text = str(value if value is not None else "").strip().replace(",", "")
        default_text = str(default or self._WORKER_DEFAULT_CREDITS_PER_REQUEST)
        if not raw_text or raw_text in self._WORKER_LEGACY_CREDITS_PER_REQUESTS:
            return self._worker_credit_amount_text(default_text, default_text)
        return self._worker_credit_amount_text(value, default_text)

    def _worker_seller_credit_per_token_text(self, value: Any, default: str | None = None) -> str:
        raw_text = str(value if value is not None else "").strip().replace(",", "")
        default_text = str(default or self._WORKER_DEFAULT_CREDITS_PER_TOKEN)
        if not raw_text or raw_text in self._WORKER_LEGACY_CREDITS_PER_REQUESTS:
            return self._worker_credit_amount_text(default_text, default_text)
        return self._worker_credit_amount_text(value, default_text)

    def _worker_estimated_request_credits_from_token_rate(self, credits_per_token: Any, target_output_tokens: Any) -> tuple[str, str]:
        try:
            token_count = int(target_output_tokens)
        except (TypeError, ValueError):
            token_count = self._WORKER_DEFAULT_SELLER_TARGET_TOKENS
        token_count = min(128_000, max(1, token_count))
        credits_per_token_wei = credit_decimal_text_to_wei(
            credits_per_token,
            default=self._WORKER_DEFAULT_CREDITS_PER_TOKEN,
            minimum_wei=1,
        )
        request_wei = credits_per_token_wei * token_count
        return credit_wei_to_decimal_text(request_wei), str(request_wei)

    def _worker_seller_model_text(self, value: Any) -> str:
        if isinstance(value, list):
            models = [str(item).strip() for item in value if str(item).strip()]
        else:
            models = [item.strip() for item in str(value if value is not None else "").split(",") if item.strip()]
        if not models:
            return self._WORKER_DEFAULT_SELLER_MODEL
        if len(models) == 1 and models[0] in self._WORKER_LEGACY_SELLER_MODELS:
            return self._WORKER_DEFAULT_SELLER_MODEL
        return ",".join(dict.fromkeys(models))

    def _normalize_worker_seller_availability_mode(self, value: Any, *, default: str = _WORKER_SELLER_AVAILABILITY_TOTAL_IDLE) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in self._WORKER_SELLER_AVAILABILITY_MODES:
            return normalized
        fallback = str(default or "").strip().lower()
        if fallback in self._WORKER_SELLER_AVAILABILITY_MODES:
            return fallback
        return self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE

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
        requested_ring = text(settings.get("workerRequestedRing", settings.get("worker_requested_ring")), "3")
        if requested_ring not in {"0", "1", "2", "3"}:
            requested_ring = "3"
        connection_status = text(settings.get("workerConnectionStatus", settings.get("worker_connection_status")), "disconnected")
        if connection_status not in {"disconnected", "connecting", "connected", "failed", "stale"}:
            connection_status = "disconnected"
        runtime_phase = text(settings.get("workerRuntimePhase", settings.get("worker_runtime_phase")), "not_accepting").lower()
        if runtime_phase not in {"not_accepting", "accepting", "draining"}:
            runtime_phase = "not_accepting"
        raw_seller_idle = settings.get(
            "sellerOnlyWhenIdle",
            settings.get("seller_only_when_idle", settings.get("rentalOnlyWhenIdle", settings.get("rental_only_when_idle"))),
        )
        default_availability_mode = self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE if boolish(raw_seller_idle, True) else self._WORKER_SELLER_AVAILABILITY_AI_IDLE
        seller_availability_mode = self._normalize_worker_seller_availability_mode(
            settings.get("sellerAvailabilityMode", settings.get("seller_availability_mode")),
            default=default_availability_mode,
        )
        seller_only_when_idle = seller_availability_mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE
        signed_connection = settings.get("signedWorkerConnection", settings.get("signed_worker_connection"))
        if isinstance(signed_connection, dict):
            signed_assigned_ring = text(signed_connection.get("assigned_ring"), "")
            if signed_assigned_ring not in {"0", "1", "2", "3"}:
                signed_assigned_ring = ""
            signed_wallet = text(signed_connection.get("wallet_address"), "")
            signed_message = text(signed_connection.get("message"), "")
            signed_signature = text(signed_connection.get("signature"), "")
            legacy_status = text(signed_connection.get("status"), "signed")
            hub_registered = boolish(signed_connection.get("hub_registered"), False)
            hub_registration_error = text(
                signed_connection.get(
                    "hub_registration_error",
                    signed_connection.get("registration_error", signed_connection.get("last_error")),
                ),
                "",
            )
            signed_order_status = text(signed_connection.get("signed_order_status"), "")
            if signed_order_status not in {"not_signed", "signing", "signed_locally", "expired", "invalid"}:
                if signed_wallet and signed_message and signed_signature:
                    signed_order_status = "signed_locally"
                else:
                    signed_order_status = "not_signed"
            hub_registration_status = text(signed_connection.get("hub_registration_status"), "")
            if hub_registration_status not in {"not_submitted", "submitting", "accepted", "failed", "stale"}:
                legacy_status_lower = legacy_status.lower()
                if hub_registered or legacy_status_lower in {"hub-registered", "registered"}:
                    hub_registration_status = "accepted"
                elif hub_registration_error or legacy_status_lower in {"failed", "hub-registration-failed", "registration-failed"}:
                    hub_registration_status = "failed"
                else:
                    hub_registration_status = "not_submitted"
            signed_connection = {
                "network": text(signed_connection.get("network"), selected_network),
                "requested_ring": text(signed_connection.get("requested_ring"), requested_ring),
                "wallet_address": signed_wallet,
                "credit_wallet": text(signed_connection.get("credit_wallet"), ""),
                "hub_url": self._clean_hub_url(text(signed_connection.get("hub_url"), ""), allow_empty=True),
                "chain_id": text(signed_connection.get("chain_id"), ""),
                "message": signed_message,
                "signature": signed_signature,
                "issued_at": text(signed_connection.get("issued_at"), ""),
                "signed_at": text(signed_connection.get("signed_at"), ""),
                "expires_at": text(signed_connection.get("expires_at"), ""),
                "status": legacy_status,
                "signed_order_status": signed_order_status,
                "hub_registration_status": hub_registration_status,
                "hub_registration_attempted_at": text(signed_connection.get("hub_registration_attempted_at"), ""),
                "hub_registered_at": text(signed_connection.get("hub_registered_at"), ""),
                "hub_registered": hub_registered,
                "assigned_ring": signed_assigned_ring,
                "worker_id": text(signed_connection.get("worker_id"), ""),
                "pricing_policy": text(signed_connection.get("pricing_policy"), ""),
                "hub_registration_error": hub_registration_error,
                "registration_error": text(signed_connection.get("registration_error"), ""),
                "last_error": text(signed_connection.get("last_error"), ""),
                "hub_registration": jsonable(signed_connection.get("hub_registration"), {}),
                "worker": jsonable(signed_connection.get("worker"), {}),
                "pool": jsonable(signed_connection.get("pool"), {}),
            }
        else:
            signed_connection = {}

        assigned_ring = text(settings.get("workerAssignedRing", settings.get("worker_assigned_ring")), "")
        if assigned_ring not in {"0", "1", "2", "3"}:
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
            "workerRuntimeEnabled": boolish(settings.get("workerRuntimeEnabled", settings.get("worker_runtime_enabled")), False),
            "workerRuntimePhase": runtime_phase,
            "workerRuntimeActiveJobs": intish(settings.get("workerRuntimeActiveJobs", settings.get("worker_runtime_active_jobs")), 0, minimum=0, maximum=1024),
            "workerRuntimeLastReason": text(settings.get("workerRuntimeLastReason", settings.get("worker_runtime_last_reason")), ""),
            "workerRuntimeLastCheckedAt": text(settings.get("workerRuntimeLastCheckedAt", settings.get("worker_runtime_last_checked_at")), ""),
            "workerRuntimeLastConnectedAt": text(settings.get("workerRuntimeLastConnectedAt", settings.get("worker_runtime_last_connected_at")), ""),
            "workerRuntimeLastDisconnectedAt": text(settings.get("workerRuntimeLastDisconnectedAt", settings.get("worker_runtime_last_disconnected_at")), ""),
            "workerRuntimeLastHeartbeatAt": text(settings.get("workerRuntimeLastHeartbeatAt", settings.get("worker_runtime_last_heartbeat_at")), ""),
            "workerRuntimeLastHeartbeatStatus": text(settings.get("workerRuntimeLastHeartbeatStatus", settings.get("worker_runtime_last_heartbeat_status")), ""),
            "workerRuntimeError": text(settings.get("workerRuntimeError", settings.get("worker_runtime_error")), ""),
            "remoteEnabled": boolish(settings.get("remoteEnabled", settings.get("remote_enabled")), False),
            "remoteMode": text(settings.get("remoteMode", settings.get("remote_mode")), "ask-when-busy"),
            "remoteCreditsPerToken": text(settings.get("remoteCreditsPerToken", settings.get("remote_credits_per_token")), "0.001"),
            "remoteMaxOutputTokens": intish(settings.get("remoteMaxOutputTokens", settings.get("remote_max_output_tokens")), 1024, minimum=1, maximum=128_000),
            "remoteDailyLimit": intish(settings.get("remoteDailyLimit", settings.get("remote_daily_limit")), 100000, minimum=0),
            "remoteAskBeforeSpend": boolish(settings.get("remoteAskBeforeSpend", settings.get("remote_ask_before_spend")), False),
            "remoteOnlyWhenBusy": boolish(settings.get("remoteOnlyWhenBusy", settings.get("remote_only_when_busy")), False),
            "sellerEnabled": boolish(settings.get("sellerEnabled", settings.get("seller_enabled")), False),
            "rentalEnabled": boolish(settings.get("rentalEnabled", settings.get("rental_enabled")), False),
            "sellerAvailabilityMode": seller_availability_mode,
            "sellerOnlyWhenIdle": seller_only_when_idle,
            "rentalOnlyWhenIdle": seller_only_when_idle,
            "registrationHubUrl": self._clean_hub_url(text(settings.get("registrationHubUrl", settings.get("registration_hub_url")), self.server.config.hub_url), allow_empty=True),
            "nodeId": text(settings.get("nodeId", settings.get("node_id")), "local-worker-001"),
            "endpoint": text(settings.get("endpoint"), "http://127.0.0.1:8771"),
            "models": self._worker_seller_model_text(settings.get("models", self._WORKER_DEFAULT_SELLER_MODEL)),
            "sellerTargetTokens": intish(
                settings.get(
                    "sellerTargetTokens",
                    settings.get("seller_target_tokens", settings.get("targetOutputTokens", settings.get("target_output_tokens"))),
                ),
                self._WORKER_DEFAULT_SELLER_TARGET_TOKENS,
                minimum=1,
                maximum=128_000,
            ),
            "capability": text(settings.get("capability"), "chat.completions"),
            "sellerCreditsPerToken": self._worker_seller_credit_per_token_text(
                settings.get(
                    "sellerCreditsPerToken",
                    settings.get("seller_credits_per_token", settings.get("creditsPerToken", settings.get("credits_per_token", settings.get("creditsPerRequest", settings.get("credits_per_request"))))),
                ),
                self._WORKER_DEFAULT_CREDITS_PER_TOKEN,
            ),
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


    def _worker_runtime_signed_connection_registered(self, settings: dict[str, Any]) -> bool:
        signed = settings.get("signedWorkerConnection")
        if not isinstance(signed, dict):
            return False
        explicit_hub_status = str(signed.get("hub_registration_status") or "").strip()
        if explicit_hub_status:
            return explicit_hub_status == "accepted" and bool(signed.get("hub_registered"))
        if str(signed.get("status") or "") in {"hub-registered", "registered"}:
            return True
        return bool(signed.get("hub_registered"))

    def _worker_runtime_worker_id(self, settings: dict[str, Any]) -> str:
        signed = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        worker = signed.get("worker") if isinstance(signed.get("worker"), dict) else {}
        return str(
            signed.get("worker_id")
            or worker.get("worker_id")
            or worker.get("node_id")
            or settings.get("workerRegisteredId")
            or settings.get("nodeId")
            or ""
        ).strip()

    def _worker_runtime_instance_id(self, settings: dict[str, Any]) -> str:
        signed = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        worker = signed.get("worker") if isinstance(signed.get("worker"), dict) else {}
        worker_id = self._worker_runtime_worker_id(settings)
        return str(
            worker.get("worker_instance_id")
            or signed.get("worker_instance_id")
            or worker_id
        ).strip()

    def _worker_runtime_hub_url(self, settings: dict[str, Any]) -> str:
        signed = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        return self._clean_hub_url(
            str(settings.get("workerConnectedHubUrl") or signed.get("hub_url") or settings.get("registrationHubUrl") or self.server.config.hub_url),
            allow_empty=True,
        )

    def _worker_local_ai_capacity_snapshot(self, *, max_local_concurrency: int = 1) -> dict[str, Any]:
        manager = getattr(getattr(self, "server", None), "chat_ai_processes", None)
        snapshot_method = getattr(manager, "local_ai_capacity_snapshot", None)
        if callable(snapshot_method):
            snapshot = snapshot_method(thread_id="", max_local_concurrency=max_local_concurrency)
            return snapshot if isinstance(snapshot, dict) else {"ok": False, "available_now": False, "busy": True, "reason_code": "invalid_local_ai_capacity"}
        return {
            "ok": True,
            "scope": "local-ai",
            "available_now": True,
            "busy": False,
            "reason_code": "local_ai_capacity_unavailable_assumed_idle",
            "user_message": "Local AI capacity monitor is unavailable; assuming idle for this app-level worker check.",
            "active_run_count": 0,
            "max_local_concurrency": max(1, int(max_local_concurrency or 1)),
            "active_thread_ids": [],
            "active_runs": [],
        }

    def _worker_ring_label(self, value: Any) -> str:
        ring = str(value if value is not None else "").strip()
        if ring == "0":
            return "Ring 0 - Operator / direct whitelist"
        if ring == "1":
            return "Ring 1 - Protected trusted worker"
        if ring == "2":
            return "Ring 2 - Public"
        if ring == "3":
            return "Ring 3 - Public untrusted"
        return ""

    def _worker_runtime_signed_order_state(self, settings: dict[str, Any]) -> dict[str, Any]:
        signed = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        signature = str(signed.get("signature") or "").strip()
        message = str(signed.get("message") or "").strip()
        wallet = str(signed.get("wallet_address") or "").strip()
        credit_wallet = str(signed.get("credit_wallet") or wallet).strip()
        has_signed_order = bool(signature and message and wallet)
        status = str(signed.get("signed_order_status") or "").strip()
        if status not in {"not_signed", "signing", "signed_locally", "expired", "invalid"}:
            status = "signed_locally" if has_signed_order else "not_signed"
        if not has_signed_order and status not in {"signing", "invalid"}:
            status = "not_signed"
        labels = {
            "not_signed": "Not signed",
            "signing": "Signing",
            "signed_locally": "Signed locally",
            "expired": "Expired",
            "invalid": "Invalid",
        }
        return {
            "status": status,
            "label": labels.get(status, "Not signed"),
            "signedAt": str(signed.get("signed_at") or ""),
            "expiresAt": str(signed.get("expires_at") or ""),
            "wallet": wallet,
            "creditWallet": credit_wallet,
            "rawStatus": str(signed.get("status") or ""),
        }

    def _worker_runtime_hub_registration_state(self, settings: dict[str, Any]) -> dict[str, Any]:
        signed = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        registration = settings.get("workerHubRegistration") if isinstance(settings.get("workerHubRegistration"), dict) else {}
        signed_registration = signed.get("hub_registration") if isinstance(signed.get("hub_registration"), dict) else {}
        registration = registration or signed_registration
        raw_status = str(signed.get("status") or "").strip().lower()
        last_error = str(
            signed.get("hub_registration_error")
            or signed.get("registration_error")
            or signed.get("last_error")
            or (registration.get("error") if isinstance(registration, dict) else "")
            or settings.get("workerConnectionError")
            or ""
        ).strip()
        status = str(signed.get("hub_registration_status") or "").strip()
        if status not in {"not_submitted", "submitting", "accepted", "failed", "stale"}:
            registered = self._worker_runtime_signed_connection_registered(settings)
            failed = bool(last_error) or raw_status in {"failed", "hub-registration-failed", "registration-failed"}
            if registered:
                status = "accepted"
            elif failed:
                status = "failed"
            else:
                status = "not_submitted"
        elif status == "accepted" and not self._worker_runtime_signed_connection_registered(settings):
            status = "stale"
        labels = {
            "not_submitted": "Not submitted",
            "submitting": "Submitting",
            "accepted": "Accepted",
            "failed": "Failed",
            "stale": "Stale",
        }
        return {
            "status": status,
            "label": labels.get(status, "Not submitted"),
            "lastError": last_error,
            "attemptedAt": str(signed.get("hub_registration_attempted_at") or ""),
            "registeredAt": str(signed.get("hub_registered_at") or ""),
            "rawStatus": raw_status,
        }

    def _worker_runtime_local_policy(self, settings: dict[str, Any], *, user_activity: dict[str, Any] | None = None, active_jobs: int = 0) -> dict[str, Any]:
        seller_enabled = bool(settings.get("sellerEnabled"))
        availability_mode = self._normalize_worker_seller_availability_mode(settings.get("sellerAvailabilityMode"))
        only_when_idle = availability_mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE
        local_ai_capacity: dict[str, Any] | None = None
        active_ai_jobs = 0
        reasons: list[str] = []

        if not seller_enabled:
            reasons.append("Accept paid jobs is off.")

        if only_when_idle:
            if user_activity is None:
                user_activity = collect_windows_user_activity()
            active = user_activity.get("active") if isinstance(user_activity, dict) else None
            if active is True:
                reasons.append("Waiting for computer to be idle. Windows reports an active interactive user session.")
            elif active is not False:
                reason = str(user_activity.get("reason") or "idle status unavailable") if isinstance(user_activity, dict) else "idle status unavailable"
                reasons.append(f"Waiting for computer idle status. Windows quser could not verify idle status: {reason}.")
        else:
            user_activity = None
            local_ai_capacity = self._worker_local_ai_capacity_snapshot(max_local_concurrency=1)
            try:
                active_ai_jobs = max(0, int(local_ai_capacity.get("active_run_count", 0) or 0))
            except (TypeError, ValueError):
                active_ai_jobs = 0
            ai_available = bool(local_ai_capacity.get("available_now"))
            # While this app is already running a worker job, the local AI slot is
            # expected to be busy. Keep the session alive as busy instead of
            # interpreting the app's own job as a policy failure.
            if active_jobs <= 0 and not ai_available:
                message = str(local_ai_capacity.get("user_message") or local_ai_capacity.get("reason_code") or "").strip()
                reasons.append(f"Local AI is busy. {message}".strip())

        allowed = not reasons
        if allowed and only_when_idle:
            reason = "Computer is idle."
        elif allowed:
            reason = "AI is idle."
        else:
            reason = " ".join(reasons)

        return {
            "enabled": seller_enabled,
            "mode": availability_mode,
            "allowed": allowed,
            "label": "Allowed" if allowed else "Blocked",
            "reason": reason,
            "activeAiJobs": active_ai_jobs,
            "active_ai_jobs": active_ai_jobs,
            "user_activity": user_activity,
            "local_ai_capacity": local_ai_capacity,
            "source": "windows_quser_v1" if only_when_idle else "local_ai_capacity_v1",
        }

    def _worker_runtime_policy(self, settings: dict[str, Any], *, user_activity: dict[str, Any] | None = None, active_jobs: int = 0) -> dict[str, Any]:
        """Return whether the app may currently announce availability to the Hub.

        The runtime decision still requires identity, signed order, Hub registration,
        and a Hub URL.  The nested ``local_policy`` field stays independent so the
        UI can truthfully show that local policy may allow work even while another
        blocker, such as Hub registration, keeps the runtime from accepting work.
        """

        selected_network = str(settings.get("selectedNetwork") or "none")
        worker_id = self._worker_runtime_worker_id(settings)
        hub_url = self._worker_runtime_hub_url(settings)
        signed_order = self._worker_runtime_signed_order_state(settings)
        hub_registration = self._worker_runtime_hub_registration_state(settings)
        local_policy = self._worker_runtime_local_policy(settings, user_activity=user_activity, active_jobs=active_jobs)
        seller_enabled = bool(local_policy.get("enabled"))
        availability_mode = self._normalize_worker_seller_availability_mode(local_policy.get("mode"))
        only_when_idle = availability_mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE
        signed_ready = signed_order.get("status") == "signed_locally"
        hub_accepted = hub_registration.get("status") == "accepted" and self._worker_runtime_signed_connection_registered(settings)
        requirements = {
            "seller_enabled": seller_enabled,
            "network_selected": selected_network != "none",
            "signed_order": signed_ready,
            "signed_order_status": signed_order.get("status"),
            "hub_registered": hub_accepted,
            "hub_registration_status": hub_registration.get("status"),
            "worker_id_present": bool(worker_id),
            "hub_url_present": bool(hub_url),
            "availability_mode": availability_mode,
            "idle_only": only_when_idle,
            "ai_idle": availability_mode == self._WORKER_SELLER_AVAILABILITY_AI_IDLE,
            "local_policy_allowed": bool(local_policy.get("allowed")),
        }

        reasons: list[str] = []
        if not seller_enabled:
            reasons.append("Accept paid jobs is off.")
        if selected_network == "none":
            reasons.append("No worker network is selected.")
        if not signed_ready:
            signed_status = str(signed_order.get("status") or "not_signed")
            if signed_status == "signing":
                reasons.append("Wallet signature is in progress.")
            elif signed_status == "invalid":
                reasons.append("Signed connect order is invalid.")
            elif signed_status == "expired":
                reasons.append("Signed connect order expired.")
            else:
                reasons.append("Connect order has not been signed.")
        elif not hub_accepted:
            hub_status = str(hub_registration.get("status") or "not_submitted")
            if hub_status == "failed" and hub_registration.get("lastError"):
                reasons.append(f"Hub registration failed: {hub_registration['lastError']}")
            elif hub_status == "failed":
                reasons.append("Hub registration failed.")
            elif hub_status == "submitting":
                reasons.append("Signed connect order is being submitted to the Hub.")
            elif hub_status == "stale":
                reasons.append("Hub registration is stale.")
            else:
                reasons.append("Signed connect order has not been submitted to the Hub.")
        if hub_accepted and not worker_id:
            reasons.append("Worker ID is missing.")
        if selected_network != "none" and not hub_url:
            reasons.append("Hub URL is missing.")
        if not bool(local_policy.get("allowed")) and str(local_policy.get("reason") or "") not in reasons:
            reasons.append(str(local_policy.get("reason") or "Local policy blocks work."))

        allowed = not reasons
        return {
            "allowed_to_accept": allowed,
            "reason": (
                "Hub registration accepted and local policy allows work."
                if allowed
                else " ".join(reason for reason in reasons if reason)
            ),
            "requirements": requirements,
            "user_activity": local_policy.get("user_activity"),
            "local_ai_capacity": local_policy.get("local_ai_capacity"),
            "availability_mode": availability_mode,
            "source": "windows_quser_v1" if only_when_idle else "local_ai_capacity_v1",
            "local_policy": local_policy,
            "signed_order": signed_order,
            "hub_registration": hub_registration,
        }

    def _worker_runtime_models(self, settings: dict[str, Any]) -> list[str]:
        models = [item.strip() for item in self._worker_seller_model_text(settings.get("models")).split(",") if item.strip()]
        return models or [self._WORKER_DEFAULT_SELLER_MODEL]

    def _post_worker_runtime_heartbeat_to_hub(
        self,
        *,
        hub_url: str,
        settings: dict[str, Any],
        phase: str,
        hub_status: str,
        active_jobs: int,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        worker_id = self._worker_runtime_worker_id(settings)
        worker_instance_id = self._worker_runtime_instance_id(settings)
        models = self._worker_runtime_models(settings)
        if not worker_id:
            raise ValueError("Worker runtime heartbeat requires a worker id.")
        if not hub_url:
            raise ValueError("Worker runtime heartbeat requires a Hub URL.")

        signed_connection = settings.get("signedWorkerConnection")
        signed_worker = signed_connection.get("worker") if isinstance(signed_connection, dict) else {}
        stored_worker = settings.get("workerHubRegistration")
        base_worker = signed_worker if isinstance(signed_worker, dict) else {}
        if not base_worker and isinstance(stored_worker, dict):
            base_worker = stored_worker
        capabilities = dict(base_worker.get("capabilities", {})) if isinstance(base_worker.get("capabilities"), dict) else {}
        if not capabilities.get("capabilities"):
            capabilities["capabilities"] = ["chat.completions"]

        availability_mode = self._normalize_worker_seller_availability_mode(settings.get("sellerAvailabilityMode"))
        availability = {
            "accept_paid_jobs": bool(settings.get("sellerEnabled")),
            "availability_mode": availability_mode,
            "only_when_idle": availability_mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE,
            "idle_source": "windows_quser_v1" if availability_mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE else "local_ai_capacity_v1",
            "ai_idle_required": availability_mode == self._WORKER_SELLER_AVAILABILITY_AI_IDLE,
            "worker_runtime_phase": phase,
            "allowed_to_accept": bool(policy.get("allowed_to_accept")),
            "last_user_activity": policy.get("user_activity"),
            "local_ai_capacity": policy.get("local_ai_capacity"),
        }
        capabilities["availability"] = availability
        capabilities["runtime"] = {
            "phase": phase,
            "source": "main_app_worker_runtime_v1",
            "no_job_polling": True,
            "drains_before_disconnect": True,
        }
        request = Request(
            self._clean_hub_url(hub_url) + "/api/hub/v1/workers/heartbeat",
            data=json.dumps(
                {
                    "worker_node_id": worker_id,
                    "worker_instance_id": worker_instance_id,
                    "status": hub_status,
                    "model": models[0] if models else self._WORKER_DEFAULT_SELLER_MODEL,
                    "models": models,
                    "queue_depth": 0,
                    "active_requests": max(0, int(active_jobs or 0)),
                    "max_concurrency": 1,
                    "capabilities": capabilities,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
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
            raise RuntimeError("Hub returned a non-object worker heartbeat response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def _worker_runtime_phase_label(self, phase: Any) -> str:
        normalized = str(phase or "not_accepting")
        if normalized == "accepting":
            return "Accepting work"
        if normalized == "draining":
            return "Finishing current work"
        return "Not accepting"

    def _worker_runtime_primary_status(
        self,
        settings: dict[str, Any],
        *,
        phase: str,
        active_jobs: int,
        can_accept: bool,
        policy: dict[str, Any],
        heartbeat_error: str = "",
    ) -> dict[str, str]:
        signed_order = policy.get("signed_order") if isinstance(policy.get("signed_order"), dict) else self._worker_runtime_signed_order_state(settings)
        hub_registration = policy.get("hub_registration") if isinstance(policy.get("hub_registration"), dict) else self._worker_runtime_hub_registration_state(settings)
        local_policy = policy.get("local_policy") if isinstance(policy.get("local_policy"), dict) else self._worker_runtime_local_policy(settings, active_jobs=active_jobs)
        wallet = str(signed_order.get("wallet") or "").strip()
        credit_wallet = str(signed_order.get("creditWallet") or wallet).strip()
        if heartbeat_error:
            return {
                "status": "not_accepting",
                "label": "Not accepting",
                "reason": f"Hub heartbeat failed: {heartbeat_error}",
                "next": "Check the Hub connection and retry.",
            }
        if phase == "draining" and active_jobs > 0:
            return {
                "status": "draining",
                "label": "Finishing current work",
                "reason": "The worker is draining and will disconnect after active work finishes.",
                "next": "Wait for the active job to finish.",
            }
        if not bool(local_policy.get("enabled")):
            return {
                "status": "not_accepting",
                "label": "Not accepting",
                "reason": "Accept paid jobs is off.",
                "next": "Turn on Accept paid jobs when you want this computer to work.",
            }
        if not wallet or not credit_wallet:
            return {
                "status": "not_accepting",
                "label": "Not accepting",
                "reason": "Wallet is not connected.",
                "next": "Connect a wallet.",
            }
        if signed_order.get("status") != "signed_locally":
            signed_status = str(signed_order.get("status") or "not_signed")
            if signed_status == "signing":
                reason = "Wallet signature is in progress."
                next_action = "Finish the wallet signature prompt."
            elif signed_status == "invalid":
                reason = "Signed connect order is invalid."
                next_action = "Re-sign connect order."
            elif signed_status == "expired":
                reason = "Signed connect order expired."
                next_action = "Re-sign connect order."
            else:
                reason = "Connect order has not been signed."
                next_action = "Sign connect order."
            return {
                "status": "not_accepting",
                "label": "Not accepting",
                "reason": reason,
                "next": next_action,
            }
        hub_status = str(hub_registration.get("status") or "not_submitted")
        hub_accepted = hub_status == "accepted" and self._worker_runtime_signed_connection_registered(settings)
        if not hub_accepted:
            if hub_status == "failed" and hub_registration.get("lastError"):
                return {
                    "status": "not_accepting",
                    "label": "Not accepting",
                    "reason": f"Hub registration failed: {hub_registration['lastError']}",
                    "next": "Re-sign connect order.",
                }
            if hub_status == "failed":
                return {
                    "status": "not_accepting",
                    "label": "Not accepting",
                    "reason": "Hub registration failed.",
                    "next": "Re-sign connect order.",
                }
            if hub_status == "submitting":
                return {
                    "status": "not_accepting",
                    "label": "Not accepting",
                    "reason": "Signed connect order is being submitted to the Hub.",
                    "next": "Wait for Hub registration to finish.",
                }
            if hub_status == "stale":
                return {
                    "status": "not_accepting",
                    "label": "Not accepting",
                    "reason": "Hub registration is stale.",
                    "next": "Re-sign connect order.",
                }
            return {
                "status": "not_accepting",
                "label": "Not accepting",
                "reason": "Signed connect order has not been submitted to the Hub.",
                "next": "Re-sign connect order.",
            }
        if not bool(local_policy.get("allowed")):
            mode = str(local_policy.get("mode") or "")
            return {
                "status": "not_accepting",
                "label": "Not accepting",
                "reason": str(local_policy.get("reason") or "Local policy blocks work."),
                "next": (
                    "Wait until the computer is idle."
                    if mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE
                    else "Wait until local AI work finishes."
                ),
            }
        if phase == "accepting" and can_accept:
            return {
                "status": "accepting",
                "label": "Accepting work",
                "reason": "Hub registration accepted and local policy allows work.",
                "next": "Waiting for Hub job assignment.",
            }
        return {
            "status": "not_accepting",
            "label": "Not accepting",
            "reason": "Worker is not ready.",
            "next": "Check registration and local policy.",
        }

    def _worker_runtime_status_payload(
        self,
        settings: dict[str, Any],
        *,
        phase: str,
        active_jobs: int,
        can_accept: bool,
        hub_status: str,
        reason: str,
        now: str,
        heartbeat_error: str,
        heartbeat_result: dict[str, Any] | None,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        signed = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        signed_worker = signed.get("worker") if isinstance(signed.get("worker"), dict) else {}
        signed_order = policy.get("signed_order") if isinstance(policy.get("signed_order"), dict) else self._worker_runtime_signed_order_state(settings)
        hub_registration = policy.get("hub_registration") if isinstance(policy.get("hub_registration"), dict) else self._worker_runtime_hub_registration_state(settings)
        local_policy = policy.get("local_policy") if isinstance(policy.get("local_policy"), dict) else self._worker_runtime_local_policy(settings, active_jobs=active_jobs)
        primary = self._worker_runtime_primary_status(
            settings,
            phase=phase,
            active_jobs=active_jobs,
            can_accept=can_accept,
            policy=policy,
            heartbeat_error=heartbeat_error,
        )
        requested_ring = str(settings.get("workerRequestedRing") or signed.get("requested_ring") or "3")
        assigned_ring = str(settings.get("workerAssignedRing") or signed.get("assigned_ring") or signed_worker.get("assigned_ring") or "")
        worker_id = self._worker_runtime_worker_id(settings)
        pricing_policy = str(settings.get("workerPricingPolicy") or signed.get("pricing_policy") or signed_worker.get("pricing_policy") or "")
        wallet = str(signed_order.get("wallet") or signed.get("wallet_address") or "").strip()
        credit_wallet = str(signed_order.get("creditWallet") or signed.get("credit_wallet") or wallet).strip()
        last_heartbeat_status = str(settings.get("workerRuntimeLastHeartbeatStatus") or "")
        hub_availability = last_heartbeat_status or (hub_status if hub_registration.get("status") == "accepted" else "not_announced")
        runtime_last_error = str(heartbeat_error or settings.get("workerRuntimeError") or hub_registration.get("lastError") or "")
        return {
            "ok": True,
            "status": primary["status"],
            "statusLabel": primary["label"],
            "reason": primary["reason"],
            "next": primary["next"],
            "identity": {
                "wallet": wallet,
                "creditWallet": credit_wallet,
                "workerId": worker_id,
                "requestedRing": self._worker_ring_label(requested_ring) or requested_ring,
                "assignedRing": self._worker_ring_label(assigned_ring) if assigned_ring else None,
            },
            "signedOrder": signed_order,
            "hubRegistration": hub_registration,
            "localPolicy": local_policy,
            "runtime": {
                "enabled": bool(settings.get("workerRuntimeEnabled")),
                "phase": phase,
                "label": self._worker_runtime_phase_label(phase),
                "active_jobs": active_jobs,
                "activeJobs": active_jobs,
                "allowed_to_accept": can_accept,
                "allowedToAccept": can_accept,
                "hub_status": hub_status,
                "hubAvailability": hub_availability,
                "reason": reason,
                "last_checked_at": now,
                "lastCheckedAt": now,
                "last_connected_at": settings.get("workerRuntimeLastConnectedAt", ""),
                "last_disconnected_at": settings.get("workerRuntimeLastDisconnectedAt", ""),
                "last_heartbeat_at": settings.get("workerRuntimeLastHeartbeatAt", ""),
                "lastHeartbeatAt": settings.get("workerRuntimeLastHeartbeatAt", ""),
                "lastError": runtime_last_error,
                "heartbeat_error": heartbeat_error,
                "heartbeat_result": heartbeat_result,
                "policy": policy,
            },
            "worker": {
                "pricingPolicy": pricing_policy,
                "pool": settings.get("workerPool") if isinstance(settings.get("workerPool"), dict) else None,
            },
            "settings": settings,
        }

    def _worker_runtime_transition(
        self,
        settings: dict[str, Any],
        *,
        action: str = "sync",
        active_jobs: int | None = None,
        send_heartbeat: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        cleaned = self._sanitize_worker_settings(settings)
        action = str(action or "sync").strip().lower()
        if action not in {"sync", "activate", "deactivate", "job-start", "job-finish"}:
            raise ValueError("Worker runtime action must be sync, activate, deactivate, job-start, or job-finish.")

        if action == "activate":
            # Legacy callers used to toggle a separate runtime latch.  The golden
            # path now derives worker availability from the saved seller policy,
            # so activation is an alias for enabling paid jobs.
            cleaned["sellerEnabled"] = True
            cleaned["rentalEnabled"] = True
        elif action == "deactivate":
            # Deactivation is likewise just the saved seller policy going false.
            # Active work drains before the Hub sees the worker offline.
            cleaned["sellerEnabled"] = False
            cleaned["rentalEnabled"] = False

        previous_phase = str(cleaned.get("workerRuntimePhase") or "not_accepting")
        previous_active = max(0, int(cleaned.get("workerRuntimeActiveJobs", 0) or 0))
        if active_jobs is None:
            active = previous_active
            if action == "job-start":
                active += 1
            elif action == "job-finish":
                active = max(0, active - 1)
        else:
            active = max(0, int(active_jobs or 0))

        policy = self._worker_runtime_policy(cleaned, active_jobs=active)
        runtime_enabled = bool(cleaned.get("sellerEnabled"))
        cleaned["workerRuntimeEnabled"] = runtime_enabled
        can_accept = runtime_enabled and bool(policy.get("allowed_to_accept"))

        if can_accept:
            phase = "accepting"
            hub_status = "busy" if active > 0 else "available"
        elif active > 0 and previous_phase in {"accepting", "draining"}:
            phase = "draining"
            hub_status = "draining"
        else:
            phase = "not_accepting"
            hub_status = "offline"

        now = datetime.now(timezone.utc).isoformat()
        reason = str(policy.get("reason") or "")
        heartbeat_result: dict[str, Any] | None = None
        heartbeat_error = ""
        hub_url = self._worker_runtime_hub_url(cleaned)
        should_heartbeat = send_heartbeat and self._worker_runtime_signed_connection_registered(cleaned) and bool(hub_url)
        if should_heartbeat:
            try:
                heartbeat_result = self._post_worker_runtime_heartbeat_to_hub(
                    hub_url=hub_url,
                    settings=cleaned,
                    phase=phase,
                    hub_status=hub_status,
                    active_jobs=active,
                    policy=policy,
                )
            except Exception as exc:
                heartbeat_error = str(exc)
                if phase == "accepting":
                    phase = "not_accepting"
                    hub_status = "offline"
                    reason = f"Hub heartbeat failed: {heartbeat_error}"

        if previous_phase != "accepting" and phase == "accepting":
            cleaned["workerRuntimeLastConnectedAt"] = now
        if previous_phase != "not_accepting" and phase == "not_accepting":
            cleaned["workerRuntimeLastDisconnectedAt"] = now
        cleaned["workerRuntimePhase"] = phase
        cleaned["workerRuntimeActiveJobs"] = active
        cleaned["workerRuntimeLastReason"] = reason
        cleaned["workerRuntimeLastCheckedAt"] = now
        cleaned["workerRuntimeLastHeartbeatStatus"] = hub_status if should_heartbeat and not heartbeat_error else ""
        if should_heartbeat and not heartbeat_error:
            cleaned["workerRuntimeLastHeartbeatAt"] = now
        cleaned["workerRuntimeError"] = heartbeat_error
        saved = self._save_worker_settings(cleaned)

        status = self._worker_runtime_status_payload(
            saved,
            phase=phase,
            active_jobs=active,
            can_accept=can_accept,
            hub_status=hub_status,
            reason=reason,
            now=now,
            heartbeat_error=heartbeat_error,
            heartbeat_result=heartbeat_result,
            policy=policy,
        )
        return saved, status

    def _handle_worker_runtime_status(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker runtime status is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            settings = self._load_worker_settings()
            _saved, status = self._worker_runtime_transition(settings, action="sync", send_heartbeat=False)
            self.server.signal("api-worker-runtime-status", phase=status["runtime"]["phase"], allowed=status["runtime"]["allowed_to_accept"])
            self._send_json(status)
        except Exception as exc:
            self.server.signal("api-worker-runtime-status-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_runtime_sync(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker runtime sync is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            action = str(body.get("action") or "sync")
            active_jobs_raw = body.get("active_jobs")
            active_jobs = int(active_jobs_raw) if active_jobs_raw is not None else None
            settings = self._load_worker_settings()
            incoming_settings = body.get("settings")
            if isinstance(incoming_settings, dict):
                settings.update(self._sanitize_worker_settings(incoming_settings))
                settings = self._save_worker_settings(settings)
            _saved, status = self._worker_runtime_transition(settings, action=action, active_jobs=active_jobs, send_heartbeat=True)
            self.server.signal(
                "api-worker-runtime-sync",
                action=action,
                phase=status["runtime"]["phase"],
                allowed=status["runtime"]["allowed_to_accept"],
                active_jobs=status["runtime"]["active_jobs"],
            )
            self._send_json(status)
        except Exception as exc:
            self.server.signal("api-worker-runtime-sync-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


    def _worker_network_order(self) -> list[str]:
        return ["mainnet", "testnet", "test", "dev"]

    def _worker_ring_order(self) -> list[dict[str, str]]:
        return [
            {"ring": "0", "label": "Ring 0 - Operator", "description": "operator / direct whitelist"},
            {"ring": "1", "label": "Ring 1 - Protected", "description": "protected trusted workers"},
            {"ring": "2", "label": "Ring 2 - Public", "description": "public workers"},
            {"ring": "3", "label": "Ring 3 - Public untrusted", "description": "public untrusted workers"},
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
        ring = str(value if value is not None else "3").strip()
        if ring not in {"0", "1", "2", "3"}:
            raise ValueError("Worker ring must be one of 0, 1, 2, or 3.")
        return ring

    def _worker_network_session_payload(self, settings: dict[str, Any], *, check_hub: bool = False) -> dict[str, Any]:
        profiles = self._worker_network_profiles_payload()
        profiles_by_key = {str(profile["network"]): profile for profile in profiles}
        selected = self._normalize_worker_network_key(settings.get("selectedNetwork", "none"))
        requested_ring = self._normalize_worker_ring(settings.get("workerRequestedRing", "3"))
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
            requested_ring = self._normalize_worker_ring(body.get("requested_ring", "3"))
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
            requested_ring = self._normalize_worker_ring(body.get("requested_ring", "3"))
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
            message_payload: dict[str, Any] = {}
            try:
                parsed_message = json.loads(message)
                if isinstance(parsed_message, dict):
                    message_payload = parsed_message
            except (TypeError, ValueError):
                message_payload = {}
            issued_at = str(message_payload.get("issued_at") or "")
            expires_at = str(message_payload.get("expires_at") or "")

            signed_at = datetime.now(timezone.utc).isoformat()
            signed_connection = {
                "network": selected,
                "requested_ring": requested_ring,
                "wallet_address": wallet_address,
                "credit_wallet": wallet_address,
                "hub_url": hub_url,
                "chain_id": chain_id,
                "message": message,
                "signature": signature,
                "issued_at": issued_at,
                "signed_at": signed_at,
                "expires_at": expires_at,
                "status": "signed",
                "signed_order_status": "signed_locally",
                "hub_registration_status": "not_submitted",
                "hub_registration_error": "",
                "hub_registration_attempted_at": "",
                "hub_registered_at": "",
                "hub_registered": False,
            }
            if not expires_at:
                invalid_error = "Signed connect order message is missing expires_at; re-sign connect order."
                signed_connection.update(
                    {
                        "status": "invalid",
                        "signed_order_status": "invalid",
                        "hub_registration_status": "not_submitted",
                        "hub_registration_error": invalid_error,
                        "last_error": invalid_error,
                        "worker": registration_payload,
                    }
                )
                settings["workerRequestedRing"] = requested_ring
                settings["workerAssignedRing"] = ""
                settings["workerRegisteredId"] = ""
                settings["workerPricingPolicy"] = ""
                settings["workerConnectedHubUrl"] = hub_url
                settings["workerConnectionStatus"] = "failed"
                settings["workerConnectionError"] = invalid_error
                settings["workerHubRegistration"] = {}
                settings["workerPool"] = {}
                settings["signedWorkerConnection"] = signed_connection
                self._save_worker_settings(settings)
                raise ValueError(invalid_error)
            settings["workerRequestedRing"] = requested_ring
            settings["workerAssignedRing"] = ""
            settings["workerRegisteredId"] = ""
            settings["workerPricingPolicy"] = ""
            settings["workerConnectedHubUrl"] = hub_url
            settings["workerConnectionStatus"] = "connected"
            settings["workerConnectionError"] = ""
            settings["workerHubRegistration"] = {}
            settings["workerPool"] = {}
            settings["signedWorkerConnection"] = signed_connection
            settings = self._save_worker_settings(settings)

            attempted_at = datetime.now(timezone.utc).isoformat()
            signed_connection = dict(settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else signed_connection)
            signed_connection.update(
                {
                    "status": "registering-with-hub",
                    "signed_order_status": "signed_locally",
                    "hub_registration_status": "submitting",
                    "hub_registration_attempted_at": attempted_at,
                    "hub_registration_error": "",
                    "last_error": "",
                    "hub_registered": False,
                }
            )
            settings["signedWorkerConnection"] = signed_connection
            settings = self._save_worker_settings(settings)

            try:
                registration = self._post_worker_connect_order_to_hub(
                    hub_url=hub_url,
                    payload={
                        "signed_connection": signed_connection,
                        "worker": registration_payload,
                    },
                )
            except Exception as register_exc:
                registration_error = str(register_exc)
                failed_registration = {
                    "status": "failed",
                    "label": "Failed",
                    "hub_url": hub_url,
                    "error": registration_error,
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                }
                signed_connection.update(
                    {
                        "status": "hub-registration-failed",
                        "signed_order_status": "signed_locally",
                        "hub_registration_status": "failed",
                        "hub_registered": False,
                        "hub_registration_error": registration_error,
                        "last_error": registration_error,
                        "worker": registration_payload,
                        "hub_registration": failed_registration,
                    }
                )
                settings["workerAssignedRing"] = ""
                settings["workerRegisteredId"] = ""
                settings["workerPricingPolicy"] = ""
                settings["workerConnectedHubUrl"] = hub_url
                settings["workerConnectionStatus"] = "failed"
                settings["workerConnectionError"] = registration_error
                settings["workerHubRegistration"] = failed_registration
                settings["workerPool"] = {}
                settings["signedWorkerConnection"] = signed_connection
                self._save_worker_settings(settings)
                raise
            worker = registration.get("worker") if isinstance(registration.get("worker"), dict) else {}
            pool = registration.get("pool") if isinstance(registration.get("pool"), dict) else {}
            assigned_ring = str(registration.get("assigned_ring") or worker.get("assigned_ring") or requested_ring)
            worker_id = str(registration.get("worker_id") or worker.get("worker_id") or worker.get("node_id") or registration_payload["node_id"])
            pricing_policy = str(registration.get("pricing_policy") or worker.get("pricing_policy") or worker.get("capabilities", {}).get("pricing_policy", ""))
            signed_connection.update(
                {
                    "status": "hub-registered",
                    "signed_order_status": "signed_locally",
                    "hub_registration_status": "accepted",
                    "hub_registered": True,
                    "hub_registered_at": datetime.now(timezone.utc).isoformat(),
                    "hub_registration_error": "",
                    "last_error": "",
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

    def _worker_wallet_funding_deployment_manifest_candidates(self) -> list[Path]:
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
                candidates.append(root / "runtime" / "deployments" / "dev" / "latest.json")

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
        for path in self._worker_wallet_funding_deployment_manifest_candidates():
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
                    "deployment_manifest_path": str(path),
                    "source": source,
                    "funding_model": "hub_credit_bridge_escrow_wallet_v2",
                }
            except Exception as exc:
                last_error = f"{path}: {exc}"
                continue
        detail = f" Last error: {last_error}" if last_error else ""
        raise FileNotFoundError("Could not find runtime/deployments/dev/latest.json with hub_credit_bridge_escrow metadata." + detail)

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

    def _wallet_dns_control_profiles_path(self) -> Path:
        return self.server.debug_root / "wallet_dns_control_profiles.json"

    def _load_wallet_dns_control_profiles(self) -> list[dict[str, Any]]:
        path = self._wallet_dns_control_profiles_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except json.JSONDecodeError:
            return []
        profiles = payload.get("profiles") if isinstance(payload, dict) else payload
        if not isinstance(profiles, list):
            return []
        return [dict(item) for item in profiles if isinstance(item, dict)][:100]

    def _save_wallet_dns_control_profiles(self, profiles: list[dict[str, Any]]) -> None:
        path = self._wallet_dns_control_profiles_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"profiles": [dict(item) for item in profiles[:100]]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _normalize_wallet_dns_control_mode(self, value: Any) -> str:
        mode = str(value or "cloudflare").strip().lower()
        if mode in {"cloudflare", "cloudflare-managed", "cf"}:
            return "cloudflare"
        if mode in {"self-hosted", "self_hosted", "own-dns", "own_dns", "authoritative"}:
            return "self-hosted"
        raise ValueError("provider_mode must be either cloudflare or self-hosted.")

    def _normalize_wallet_dns_control_text(self, value: Any, *, field: str, required: bool = True, max_length: int = 253) -> str:
        text = str(value or "").strip()
        if required and not text:
            raise ValueError(f"{field} is required.")
        if len(text) > max_length:
            raise ValueError(f"{field} must be {max_length} characters or fewer.")
        if any(ch in text for ch in "\r\n\x00"):
            raise ValueError(f"{field} contains unsafe characters.")
        return text

    def _normalize_wallet_dns_control_zone(self, value: Any) -> str:
        zone = self._normalize_wallet_dns_control_text(value, field="zone").rstrip(".").lower()
        if not re.fullmatch(r"(?=.{1,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", zone):
            raise ValueError("zone must be a valid domain name.")
        return zone

    def _normalize_wallet_dns_control_record_name(self, value: Any) -> str:
        name = self._normalize_wallet_dns_control_text(value or "@", field="record_name", max_length=253)
        return name.rstrip(".") or "@"

    def _normalize_wallet_dns_control_record_type(self, value: Any) -> str:
        record_type = str(value or "A").strip().upper()
        allowed = {"A", "AAAA", "CNAME", "MX", "TXT", "NS", "CAA", "SRV"}
        if record_type not in allowed:
            raise ValueError("record_type must be one of: A, AAAA, CNAME, MX, TXT, NS, CAA, SRV.")
        return record_type

    def _normalize_wallet_dns_control_ttl(self, value: Any) -> int:
        try:
            ttl = int(str(value or "300").strip())
        except (TypeError, ValueError):
            raise ValueError("ttl must be a whole number of seconds.") from None
        if ttl < 60 or ttl > 86400:
            raise ValueError("ttl must be between 60 and 86400 seconds.")
        return ttl

    def _wallet_dns_control_defaults(self) -> dict[str, Any]:
        return {
            "provider_mode": "cloudflare",
            "ttl": 300,
            "record_type": "A",
            "cloudflare_token_env": "CLOUDFLARE_API_TOKEN",
            "self_hosted_nameserver_hint": "ns1.example.com",
        }

    def _wallet_dns_control_status_message(self, profile: dict[str, Any] | None = None) -> str:
        if not profile:
            return "Connect wallet, choose Cloudflare or self-hosted DNS, then save a control profile."
        provider = "Cloudflare DNS" if profile.get("provider_mode") == "cloudflare" else "self-hosted authoritative DNS"
        return f"Saved {provider} control profile for {profile.get('zone', 'zone')}."

    def _handle_wallet_dns_control_profiles_load(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Wallet DNS control is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            profiles = self._load_wallet_dns_control_profiles()
            self.server.signal("api-wallet-dns-control-load", count=len(profiles))
            self._send_json({
                "ok": True,
                "defaults": self._wallet_dns_control_defaults(),
                "status_message": self._wallet_dns_control_status_message(profiles[0] if profiles else None),
                "profiles": profiles[:20],
            })
        except Exception as exc:
            self.server.signal("api-wallet-dns-control-load-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_wallet_dns_control_profile_save(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Wallet DNS control is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            owner_wallet = self._normalize_worker_wallet_address(body.get("owner_wallet", body.get("wallet_address", "")))
            provider_mode = self._normalize_wallet_dns_control_mode(body.get("provider_mode"))
            zone = self._normalize_wallet_dns_control_zone(body.get("zone"))
            record_name = self._normalize_wallet_dns_control_record_name(body.get("record_name", "@"))
            record_type = self._normalize_wallet_dns_control_record_type(body.get("record_type", "A"))
            record_value = self._normalize_wallet_dns_control_text(body.get("record_value"), field="record_value", max_length=2048)
            ttl = self._normalize_wallet_dns_control_ttl(body.get("ttl", 300))
            proxied = self._coerce_bool(body.get("proxied"), default=False)
            nameserver_host = self._normalize_wallet_dns_control_text(body.get("nameserver_host", ""), field="nameserver_host", required=False)
            admin_url = self._normalize_wallet_dns_control_text(body.get("admin_url", ""), field="admin_url", required=False, max_length=500)

            if provider_mode == "self-hosted" and not nameserver_host and not admin_url:
                raise ValueError("self-hosted DNS requires nameserver_host or admin_url.")
            if provider_mode == "cloudflare":
                nameserver_host = ""
                admin_url = ""

            profile = {
                "id": f"dns_profile_{uuid.uuid4().hex[:16]}",
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "owner_wallet": owner_wallet,
                "provider_mode": provider_mode,
                "zone": zone,
                "record_name": record_name,
                "record_type": record_type,
                "record_value": record_value,
                "ttl": ttl,
                "proxied": bool(proxied) if provider_mode == "cloudflare" else False,
                "nameserver_host": nameserver_host,
                "admin_url": admin_url,
                "control_actions": [
                    "cloudflare_dns_records" if provider_mode == "cloudflare" else "self_hosted_authoritative_zone",
                    "wallet_owned_dns_profile",
                ],
                "secret_policy": "Store provider API tokens outside the browser; use environment variables or the self-hosted DNS admin service.",
            }
            profiles = [profile, *self._load_wallet_dns_control_profiles()]
            self._save_wallet_dns_control_profiles(profiles)
            self.server.signal(
                "api-wallet-dns-control-save",
                provider_mode=provider_mode,
                zone=zone,
                owner_wallet=owner_wallet,
            )
            self._send_json({
                "ok": True,
                "profile": profile,
                "profiles": profiles[:20],
                "defaults": self._wallet_dns_control_defaults(),
                "status_message": self._wallet_dns_control_status_message(profile),
            })
        except Exception as exc:
            self.server.signal("api-wallet-dns-control-save-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _wallet_agent_credit_grants_path(self) -> Path:
        return self.server.debug_root / "wallet_agent_credit_grants.json"

    def _load_wallet_agent_credit_grants_history(self) -> list[dict[str, Any]]:
        path = self._wallet_agent_credit_grants_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except json.JSONDecodeError:
            return []
        grants = payload.get("grants") if isinstance(payload, dict) else payload
        if not isinstance(grants, list):
            return []
        return [dict(item) for item in grants if isinstance(item, dict)][:100]

    def _save_wallet_agent_credit_grants_history(self, grants: list[dict[str, Any]]) -> None:
        path = self._wallet_agent_credit_grants_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"grants": [dict(item) for item in grants[:100]]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _normalize_wallet_agent_credit_grant_amount(self, value: Any) -> int:
        try:
            credits = int(str(value or "").strip())
        except (TypeError, ValueError):
            raise ValueError("credits must be a whole number.") from None
        if credits < 1 or credits > 100:
            raise ValueError("credits must be between 1 and 100 for wallet helper grants.")
        return credits

    def _handle_wallet_agent_credit_grants_load(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Wallet agent credit grants are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            grants = self._load_wallet_agent_credit_grants_history()
            hub_url = self._clean_hub_url(str(getattr(self.server.config, "hub_url", "") or "http://127.0.0.1:8770"))
            self.server.signal("api-wallet-agent-credit-grants-load", count=len(grants), hub_url=hub_url)
            self._send_json({"ok": True, "hub_url": hub_url, "grants": grants[:20]})
        except Exception as exc:
            self.server.signal("api-wallet-agent-credit-grants-load-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_wallet_agent_credit_grant_create(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Wallet agent credit grants are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            issuer_wallet = self._normalize_worker_wallet_address(body.get("issuer_wallet"))
            recipient_wallet = self._normalize_worker_wallet_address(body.get("recipient_wallet", body.get("account_id", "")))
            credits = self._normalize_wallet_agent_credit_grant_amount(body.get("credits"))
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url or "http://127.0.0.1:8770"))
            memo = str(body.get("memo") or "Agent helper credits for parallel verification workers.").strip()
            if not memo:
                memo = "Agent helper credits for parallel verification workers."

            forwarded = {
                "account_id": recipient_wallet,
                "owner_address": recipient_wallet,
                "credits": credits,
                "memo": memo,
                "metadata": {
                    "source": "wallet_agent_credit_grant",
                    "agent_credit_grant": True,
                    "issuer_wallet": issuer_wallet,
                    "recipient_wallet": recipient_wallet,
                },
            }
            result = self._post_wallet_agent_credit_grant_to_hub(hub_url=hub_url, payload=forwarded)
            transaction = result.get("transaction") if isinstance(result.get("transaction"), dict) else {}
            grant = {
                "id": transaction.get("transaction_id") or f"agent_grant_{uuid.uuid4().hex[:16]}",
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "hub_url": hub_url,
                "issuer_wallet": issuer_wallet,
                "recipient_wallet": recipient_wallet,
                "account_id": recipient_wallet,
                "credits": credits,
                "memo": memo,
                "transaction_id": str(transaction.get("transaction_id", "")),
            }
            grants = [grant, *self._load_wallet_agent_credit_grants_history()]
            self._save_wallet_agent_credit_grants_history(grants)
            self.server.signal(
                "api-wallet-agent-credit-grant-create",
                hub_url=hub_url,
                issuer_wallet=issuer_wallet,
                recipient_wallet=recipient_wallet,
                credits=credits,
                transaction_id=grant["transaction_id"],
            )
            self._send_json({"ok": True, "hub_url": hub_url, "grant": grant, "hub_result": result})
        except Exception as exc:
            self.server.signal("api-wallet-agent-credit-grant-create-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _post_wallet_agent_credit_grant_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self._clean_hub_url(hub_url) + "/api/hub/v1/credits/admin/issue",
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
            raise RuntimeError("Hub returned a non-object agent credit grant response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data


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

    def _worker_seller_availability_from_payload(self, worker_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        raw_availability = worker_payload.get("availability")
        availability = dict(raw_availability) if isinstance(raw_availability, dict) else {}
        capabilities = worker_payload.get("capabilities") if isinstance(worker_payload.get("capabilities"), dict) else {}
        if not availability and isinstance(capabilities.get("availability"), dict):
            availability = dict(capabilities["availability"])

        def boolish(raw: Any, default: bool = False) -> bool:
            if isinstance(raw, bool):
                return raw
            text = str(raw or "").strip().lower()
            if text in {"1", "true", "yes", "on", "enabled", "enable"}:
                return True
            if text in {"0", "false", "no", "off", "disabled", "disable"}:
                return False
            return bool(default)

        accept_paid_jobs = boolish(availability.get("accept_paid_jobs", availability.get("seller_enabled", True)), True)
        raw_only_when_idle = availability.get("only_when_idle", availability.get("seller_only_when_idle"))
        default_mode = self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE if boolish(raw_only_when_idle, True) else self._WORKER_SELLER_AVAILABILITY_AI_IDLE
        availability_mode = self._normalize_worker_seller_availability_mode(availability.get("availability_mode"), default=default_mode)
        only_when_idle = availability_mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE
        cleaned = {
            "accept_paid_jobs": accept_paid_jobs,
            "availability_mode": availability_mode,
            "only_when_idle": only_when_idle,
            "idle_source": "windows_user_activity_v1" if only_when_idle else "local_ai_capacity_v1",
            "ai_idle_required": availability_mode == self._WORKER_SELLER_AVAILABILITY_AI_IDLE,
        }
        user_activity: dict[str, Any] | None = None
        if only_when_idle:
            user_activity = collect_windows_user_activity()
            cleaned["last_user_activity"] = user_activity
            cleaned["idle_verified"] = user_activity.get("active") is False
        else:
            cleaned["idle_verified"] = None

        return cleaned, user_activity

    def _enforce_worker_seller_availability(self, availability: dict[str, Any], user_activity: dict[str, Any] | None) -> None:
        if not bool(availability.get("accept_paid_jobs")):
            raise ValueError("Accept paid jobs is off; this machine will not register a seller offer.")
        if not bool(availability.get("only_when_idle")):
            return
        active = user_activity.get("active") if isinstance(user_activity, dict) else None
        if active is True:
            raise ValueError("Only when totally idle is selected, but Windows reports an active interactive user session.")
        if active is not False:
            reason = str(user_activity.get("reason") or "idle status unavailable") if isinstance(user_activity, dict) else "idle status unavailable"
            raise ValueError(f"Only when totally idle is selected, but this machine's idle status could not be verified: {reason}.")

    def _worker_registration_payload_from_ui(self, worker_payload: dict[str, Any]) -> dict[str, Any]:
        models = [str(item).strip() for item in worker_payload.get("models", []) if str(item).strip()] if isinstance(worker_payload.get("models"), list) else []
        model = str(worker_payload.get("model") or (models[0] if models else "")).strip()
        if model and model not in models:
            models.insert(0, model)
        models_text = self._worker_seller_model_text(models)
        models = [item.strip() for item in models_text.split(",") if item.strip()]
        model = models[0] if models else ""
        if not models:
            raise ValueError("At least one worker model is required.")

        pricing = dict(worker_payload.get("pricing", {})) if isinstance(worker_payload.get("pricing"), dict) else {}

        def target_tokens(raw: Any, default: int = self._WORKER_DEFAULT_SELLER_TARGET_TOKENS) -> int:
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                parsed = int(default)
            return min(128_000, max(1, parsed))

        target_output_tokens = target_tokens(
            pricing.get(
                "target_output_tokens",
                pricing.get("target_tokens_per_request", worker_payload.get("target_output_tokens", worker_payload.get("target_tokens"))),
            )
        )
        credits_per_token = self._worker_seller_credit_per_token_text(
            pricing.get("credits_per_token", worker_payload.get("credits_per_token", worker_payload.get("sellerCreditsPerToken"))),
            self._WORKER_DEFAULT_CREDITS_PER_TOKEN,
        )
        credits_per_token_wei_source = pricing.get("credits_per_token_wei", worker_payload.get("credits_per_token_wei"))
        if credits_per_token_wei_source not in (None, ""):
            credits_per_token_wei = self._worker_credit_amount_wei_text(
                credits_per_token_wei_source,
                credits_per_token,
                value_is_wei=True,
            )
        else:
            credits_per_token_wei = self._worker_credit_amount_wei_text(credits_per_token, credits_per_token)
        estimated_credits_per_request, estimated_credits_per_request_wei = self._worker_estimated_request_credits_from_token_rate(
            credits_per_token,
            target_output_tokens,
        )
        credits_per_request = estimated_credits_per_request
        credits_per_request_wei = estimated_credits_per_request_wei
        legacy_credits_per_request = self._worker_legacy_credit_amount_ceiling_text(credits_per_request_wei, credits_per_request)

        execution = dict(worker_payload.get("execution", {})) if isinstance(worker_payload.get("execution"), dict) else {}
        execution_mode = str(execution.get("mode") or worker_payload.get("execution_mode") or "worker_pull_v0").strip() or "worker_pull_v0"
        max_concurrency = max(1, int(execution.get("max_concurrency", worker_payload.get("max_concurrency", 1)) or 1))
        capabilities = dict(worker_payload.get("capabilities", {})) if isinstance(worker_payload.get("capabilities"), dict) else {}
        capabilities.setdefault("capabilities", ["chat.completions"])
        capabilities["pricing"] = {
            "pricing_type": str(pricing.get("pricing_type") or "approx_per_token_v0"),
            "credits_per_token": credits_per_token,
            "credits_per_token_wei": credits_per_token_wei,
            "target_output_tokens": target_output_tokens,
            "estimated_credits_per_request": estimated_credits_per_request,
            "estimated_credits_per_request_wei": estimated_credits_per_request_wei,
            "credits_per_request": credits_per_request,
            "credits_per_request_wei": credits_per_request_wei,
            "unit": str(pricing.get("unit") or "compute_credit"),
        }
        capabilities["execution"] = {
            "mode": execution_mode,
            "max_concurrency": max_concurrency,
        }
        capabilities["phase12_worker_seller_offer_ui"] = True
        availability, user_activity = self._worker_seller_availability_from_payload(worker_payload)
        self._enforce_worker_seller_availability(availability, user_activity)
        capabilities["availability"] = availability
        capabilities["target_output_tokens"] = target_output_tokens

        payload = {
            "node_id": str(worker_payload.get("node_id") or "").strip(),
            "endpoint": self._clean_hub_url(str(worker_payload.get("endpoint") or "")),
            "model": model,
            "models": models,
            "credits_per_token": credits_per_token,
            "credits_per_token_wei": credits_per_token_wei,
            "estimated_credits_per_request": estimated_credits_per_request,
            "estimated_credits_per_request_wei": estimated_credits_per_request_wei,
            "credits_per_request": legacy_credits_per_request,
            "credits_per_request_wei": credits_per_request_wei,
            "target_output_tokens": target_output_tokens,
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
