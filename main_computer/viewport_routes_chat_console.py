from __future__ import annotations

from main_computer.viewport_state import *  # noqa: F401,F403
import os
from urllib.parse import parse_qs, quote, urlsplit

from main_computer.chat_ai_subprocess import (
    ChatAISubprocessBusy,
    ChatAISubprocessCancelled,
    ChatAISubprocessError,
    append_text_log,
    config_to_payload,
)
from main_computer.models import ChatResponse
from main_computer.remote_overflow import RemoteOverflowDecisionEngine, RemoteHubExecutionGateway, estimate_remote_request


def _should_inline_test_provider(provider: Any) -> bool:
    module = str(getattr(getattr(provider, "__class__", None), "__module__", "") or "")
    if not provider or module.startswith("main_computer.providers"):
        return False
    return os.environ.get("MAIN_COMPUTER_DISABLE_INLINE_TEST_PROVIDER", "").strip().lower() not in {"1", "true", "yes", "on"}


class ViewportChatConsoleRoutesMixin:
    def _chat_console_shared_variables_root(self) -> Path:
        root = (self.server.debug_root / "chat_console_shared_variables").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _chat_console_shared_variables_path(self, blob_id: str) -> Path:
        clean = str(blob_id or "").strip()
        if not re.fullmatch(r"[a-f0-9]{32}", clean):
            raise ValueError("Invalid shared variable blob id.")
        root = self._chat_console_shared_variables_root()
        path = (root / f"{clean}.json").resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("Shared variable blob path must stay inside chat console storage.") from exc
        return path

    def _chat_console_clean_shared_variables(self, value: Any, limit: int = 12000) -> dict[str, Any]:
        if not isinstance(value, dict) or isinstance(value, list):
            return {}
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key or "").strip()
            if not name or len(name) > 80 or name in {"__proto__", "constructor", "prototype"}:
                continue
            try:
                encoded = json.dumps(item, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                continue
            if not encoded or len(encoded) > limit:
                continue
            cleaned[name] = json.loads(encoded)
        return cleaned

    def _handle_chat_console_shared_variables_export(self) -> None:
        try:
            body = self._read_json()
            variables = self._chat_console_clean_shared_variables(body.get("variables") if isinstance(body, dict) else {})
            if not variables:
                raise ValueError("No shared variables were provided.")
            blob_id = uuid.uuid4().hex
            payload = {
                "version": 1,
                "kind": "chat-console-shared-variables",
                "id": blob_id,
                "variables": variables,
                "source": self._chat_console_clean_shared_variables(body.get("source") if isinstance(body, dict) else {}, limit=4000),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            path = self._chat_console_shared_variables_path(blob_id)
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
            thread_id = str(source.get("thread_id") or source.get("active_thread_id") or "").strip()
            spreadsheet_url = f"/applications/spreadsheet?chat_vars={blob_id}"
            if thread_id:
                spreadsheet_url = f"{spreadsheet_url}&thread={quote(thread_id)}"
            self.server.signal("api-chat-console-shared-variables-export", id=blob_id, count=len(variables))
            self._send_json({
                "ok": True,
                "id": blob_id,
                "count": len(variables),
                "variables": variables,
                "spreadsheet_url": spreadsheet_url,
                "thread_id": thread_id,
            })
        except Exception as exc:
            self.server.signal("api-chat-console-error", route="shared-variables-export", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _chat_console_attachment_root(self) -> Path:
        root = (self.server.debug_root / "chat_console_attachments").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _chat_console_attachment_path(self, attachment_id: str) -> Path:
        clean = str(attachment_id or "").strip()
        if not re.fullmatch(r"[a-f0-9]{32}", clean):
            raise ValueError("Invalid attachment id.")
        root = self._chat_console_attachment_root()
        path = (root / clean).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("Attachment path must stay inside chat console storage.") from exc
        return path

    def _handle_chat_console_attachment_upload(self) -> None:
        try:
            body = self._read_json()
            filename = Path(str(body.get("filename", "") or "attachment")).name
            if not filename or filename in {".", ".."}:
                raise ValueError("Attachment filename is required.")
            raw_filename = str(body.get("filename", "") or "")
            if "/" in raw_filename or "\\" in raw_filename or ".." in Path(raw_filename).parts:
                raise ValueError("Attachment filename may not contain paths.")
            mime_type = str(body.get("mime_type", "") or "application/octet-stream")
            data_base64 = str(body.get("data_base64", "") or "")
            if not data_base64:
                raise ValueError("Attachment data is required.")
            payload = base64.b64decode(data_base64.split(",", 1)[-1], validate=False)
            if len(payload) > 5 * 1024 * 1024:
                raise ValueError("Chat console attachments are limited to 5 MB.")
            attachment_id = hashlib.sha256(f"{time.time_ns()}:{filename}".encode("utf-8") + payload).hexdigest()[:32]
            path = self._chat_console_attachment_path(attachment_id)
            path.write_bytes(payload)
            metadata_path = path.with_suffix(".json")
            metadata = {
                "id": attachment_id,
                "filename": filename,
                "mime_type": mime_type,
                "size": len(payload),
                "storage_path": str(path),
                "preview_url": f"/api/applications/chat-console/attachments/{attachment_id}" if mime_type.startswith("image/") else "",
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "metadata": {},
            }
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            self.server.signal("api-chat-console-attachment-upload", attachment_id=attachment_id, bytes=len(payload))
            self._send_json({"ok": True, "attachment": metadata})
        except Exception as exc:
            self.server.signal("api-chat-console-attachment-upload-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_chat_console_attachment_get(self) -> None:
        try:
            attachment_id = urlsplit(self.path).path.rsplit("/", 1)[-1]
            path = self._chat_console_attachment_path(attachment_id)
            metadata_path = path.with_suffix(".json")
            if not path.is_file() or not metadata_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            content = path.read_bytes()
            content_type = str(metadata.get("mime_type") or "application/octet-stream")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as exc:
            self.server.signal("api-chat-console-attachment-get-error", error=exc)
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def _chat_console_evaluation_attachments(self, attachments: list[Any]) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            item = dict(attachment)
            if not item.get("data_base64") and item.get("id"):
                try:
                    path = self._chat_console_attachment_path(str(item["id"]))
                    item["data_base64"] = base64.b64encode(path.read_bytes()).decode("ascii")
                except Exception:
                    item["metadata"] = {**(item.get("metadata") if isinstance(item.get("metadata"), dict) else {}), "warning": "attachment unavailable"}
            enriched.append(item)
        return enriched

    def _chat_console_session_log_path(self, run_id: str) -> Path:
        clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(run_id or "").strip()).strip("-_.") or "chat-ai"
        root = (self.server.debug_root / "diagnostics_output" / "chat_console_ai_sessions" / clean).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root / "session.log"

    def _chat_console_thread_id(self, body: dict[str, Any], cell: dict[str, Any] | None = None) -> str:
        cell = cell if isinstance(cell, dict) else {}
        for value in (
            body.get("thread_id"),
            body.get("chat_thread_id"),
            body.get("notebook_id"),
            body.get("notebook", {}).get("id") if isinstance(body.get("notebook"), dict) else "",
            cell.get("thread_id"),
            cell.get("chat_thread_id"),
            cell.get("notebook_id"),
        ):
            text = str(value or "").strip()
            if text:
                return text
        return "default-chat-thread"

    def _append_chat_console_session_log(self, path: Path | None, record: dict[str, Any]) -> None:
        if path is None:
            return
        append_text_log(path, "chat console session event", **dict(record or {}))

    def _handle_chat_console_ai_stop(self) -> None:
        try:
            body = self._read_json()
            run_id = str(body.get("run_id") or "").strip()
            thread_id = ""
            for value in (
                body.get("thread_id"),
                body.get("chat_thread_id"),
                body.get("notebook_id"),
                body.get("notebook", {}).get("id") if isinstance(body.get("notebook"), dict) else "",
            ):
                text = str(value or "").strip()
                if text:
                    thread_id = text
                    break
            if not run_id and not thread_id:
                raise ValueError("run_id or thread_id is required to stop an AI subprocess.")
            log_path = self._chat_console_session_log_path(run_id or thread_id)
            append_text_log(log_path, "route accepted chat console AI stop", run_id=run_id, thread_id=thread_id, body=body)
            result = self.server.chat_ai_processes.stop(thread_id=thread_id, run_id=run_id, reason="ui-stop")
            self.server.activity.record(
                source="chat-console",
                kind="ai",
                time_model="snapshot",
                severity="warn" if result.get("stopped") else "info",
                title="AI subprocess stop requested",
                message=f"run_id={run_id or result.get('run_id', '')} thread_id={thread_id or result.get('thread_id', '')}",
                status="cancelled" if result.get("stopped") else "not-running",
                tags=["ai", "chat-console", "subprocess", "cancel"],
                data={
                    "run_id": run_id or result.get("run_id", ""),
                    "thread_id": thread_id or result.get("thread_id", ""),
                    "activity_filter": "ai",
                    "log_file": str(log_path),
                    "stop_result": result,
                    "rag_type": "subprocess_cancel",
                },
            )
            append_text_log(log_path, "route completed chat console AI stop", result=result)
            self._send_json(result)
        except Exception as exc:
            self.server.signal("api-chat-console-ai-stop-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_chat_console_ai_run_result(self) -> None:
        try:
            query = parse_qs(urlsplit(self.path).query)
            run_id = str((query.get("run_id") or [""])[0] or "").strip()
            thread_id = str((query.get("thread_id") or [""])[0] or "").strip()
            if not run_id and not thread_id:
                raise ValueError("run_id or thread_id is required to reconnect to an AI subprocess.")
            result = self.server.chat_ai_processes.run_result(run_id=run_id, thread_id=thread_id)
            self._send_json(result)
        except Exception as exc:
            self.server.signal("api-chat-console-ai-run-result-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_chat_console_ai_capacity(self) -> None:
        try:
            query = parse_qs(urlsplit(self.path).query)
            thread_id = str((query.get("thread_id") or [""])[0] or "").strip()
            try:
                max_local_concurrency = int((query.get("max_local_concurrency") or ["1"])[0] or "1")
            except (TypeError, ValueError):
                max_local_concurrency = 1
            result = self.server.chat_ai_processes.local_ai_capacity_snapshot(
                thread_id=thread_id,
                max_local_concurrency=max_local_concurrency,
            )
            self._send_json(result)
        except Exception as exc:
            self.server.signal("api-chat-console-ai-capacity-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)



    def _chat_console_worker_settings_path(self) -> Path:
        path_method = getattr(self, "_worker_settings_path", None)
        if callable(path_method):
            return path_method()
        return self.server.debug_root / "worker_settings.json"

    def _chat_console_load_worker_settings(self) -> dict[str, Any]:
        load_method = getattr(self, "_load_worker_settings", None)
        if callable(load_method):
            data = load_method()
            return data if isinstance(data, dict) else {}
        path = self._chat_console_worker_settings_path()
        try:
            if not path.exists():
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _chat_console_bool_setting(self, value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on", "enabled", "enable"}:
            return True
        if text in {"0", "false", "no", "off", "disabled", "disable"}:
            return False
        return bool(default)

    def _chat_console_int_setting(self, value: Any, default: int, *, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = int(default)
        return min(int(maximum), max(int(minimum), number))

    def _chat_console_decimal_setting_text(
        self,
        value: Any,
        default: str = "0.001",
        *,
        minimum: str = "0.000001",
        maximum: str = "1000000",
    ) -> str:
        try:
            number = Decimal(str(value).strip())
            if not number.is_finite():
                raise ValueError
        except Exception:
            number = Decimal(default)
        number = min(Decimal(maximum), max(Decimal(minimum), number))
        text = format(number.normalize(), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    def _chat_console_worker_multisession_cache(self) -> dict[str, Any]:
        path = self.server.debug_root / "worker_multisession_keys.json"
        try:
            if not path.exists():
                return {"keys": {}}
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("keys"), dict):
                return data
        except Exception:
            pass
        return {"keys": {}}

    def _chat_console_active_worker_multisession_key(self, *, hub_url: str = "") -> dict[str, Any] | None:
        normalized_hub_url = str(hub_url or "").strip().rstrip("/")
        records: list[dict[str, Any]] = []
        data = self._chat_console_worker_multisession_cache()
        for raw in data.get("keys", {}).values():
            if not isinstance(raw, dict):
                continue
            key_id = str(raw.get("id") or "").strip()
            wallet_address = str(raw.get("wallet_address") or raw.get("walletAddress") or "").strip().lower()
            if not key_id or not re.fullmatch(r"0x[0-9a-f]{40}", wallet_address):
                continue
            record_hub_url = str(raw.get("hub_url") or raw.get("hubUrl") or "").strip().rstrip("/")
            if normalized_hub_url and record_hub_url and record_hub_url != normalized_hub_url:
                continue
            if str(raw.get("status") or "active").strip().lower() != "active":
                continue
            records.append(
                {
                    "id": key_id,
                    "status": "active",
                    "wallet_address": wallet_address,
                    "chain_id": str(raw.get("chain_id") or raw.get("chainId") or ""),
                    "hub_url": record_hub_url,
                    "created_at": str(raw.get("created_at") or raw.get("createdAt") or ""),
                    "updated_at": str(raw.get("updated_at") or raw.get("updatedAt") or ""),
                }
            )
        records.sort(key=lambda item: (item.get("created_at", ""), item.get("updated_at", ""), item.get("id", "")), reverse=True)
        return records[0] if records else None

    def _chat_console_backend_paid_overflow_context(self, body: dict[str, Any]) -> dict[str, Any]:
        settings = self._chat_console_load_worker_settings()
        hub_url = str(settings.get("registrationHubUrl") or settings.get("registration_hub_url") or self.server.config.hub_url).strip().rstrip("/")
        active_key = self._chat_console_active_worker_multisession_key(hub_url=hub_url) or self._chat_console_active_worker_multisession_key()
        if active_key and active_key.get("hub_url"):
            hub_url = str(active_key.get("hub_url") or hub_url).strip().rstrip("/") or hub_url

        remote_enabled = self._chat_console_bool_setting(settings.get("remoteEnabled", settings.get("remote_enabled")), False)
        max_output_tokens = self._chat_console_int_setting(
            settings.get("remoteMaxOutputTokens", settings.get("remote_max_output_tokens")),
            1024,
            minimum=1,
            maximum=128_000,
        )
        credits_per_token = self._chat_console_decimal_setting_text(
            settings.get("remoteCreditsPerToken", settings.get("remote_credits_per_token")),
            "0.001",
        )

        estimate_request = dict(body or {})
        estimate_request["max_output_tokens"] = max_output_tokens
        estimate_request["credits_per_token"] = credits_per_token
        estimate = estimate_remote_request(estimate_request)
        wallet_address = str((active_key or {}).get("wallet_address") or "").strip().lower()
        multisession_key_id = str((active_key or {}).get("id") or "").strip()
        chain_id = str((active_key or {}).get("chain_id") or "0x28757b2").strip() or "0x28757b2"
        required_credits = int(getattr(estimate, "estimated_max_credits", 1) or 1)
        return {
            "settings": settings,
            "hub_url": hub_url,
            "remote_enabled": remote_enabled,
            "max_output_tokens": max_output_tokens,
            "credits_per_token": credits_per_token,
            "estimated_input_tokens": int(getattr(estimate, "estimated_input_tokens", 0) or 0),
            "estimated_max_credits": required_credits,
            "estimated_max_credits_approx": (
                getattr(estimate, "card", {}).get("details", {}).get("estimated_max_credits_approx", "")
                if isinstance(getattr(estimate, "card", {}), dict)
                else ""
            ),
            "estimate_ok": bool(getattr(estimate, "ok", False)),
            "estimate_reason_code": str(getattr(estimate, "reason_code", "") or ""),
            "wallet_address": wallet_address,
            "multisession_key_id": multisession_key_id,
            "chain_id": chain_id,
            "active_key": active_key,
        }

    def _chat_console_paid_overflow_authorization_from_backend(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "multisession_key",
            "paid_overflow_enabled": bool(context.get("remote_enabled")),
            "wallet_address": str(context.get("wallet_address") or ""),
            "multisession_key_id": str(context.get("multisession_key_id") or ""),
            "chain_id": str(context.get("chain_id") or "0x28757b2"),
            "max_output_tokens": int(context.get("max_output_tokens") or 1024),
            "credits_per_token": str(context.get("credits_per_token") or "0.001"),
            "estimated_input_tokens": int(context.get("estimated_input_tokens") or 0),
            "max_authorized_credits": int(context.get("estimated_max_credits") or 1),
            "required_credits": int(context.get("estimated_max_credits") or 1),
            "estimated_max_credits_approx": str(context.get("estimated_max_credits_approx") or ""),
            "approximation_only": True,
            "source": "local_backend_worker_settings",
        }

    def _chat_console_enrich_remote_overflow_body_with_backend_context(self, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        enriched = dict(body or {})
        context = self._chat_console_backend_paid_overflow_context(enriched)
        authorization = self._chat_console_paid_overflow_authorization_from_backend(context)
        enriched["hub_url"] = context.get("hub_url") or enriched.get("hub_url") or self.server.config.hub_url
        hub_payload = enriched.get("hub") if isinstance(enriched.get("hub"), dict) else {}
        enriched["hub"] = {**hub_payload, "url": enriched["hub_url"]}
        enriched["remote_overflow_enabled"] = bool(context.get("remote_enabled"))
        enriched["max_output_tokens"] = int(context.get("max_output_tokens") or 1024)
        enriched["credits_per_token"] = str(context.get("credits_per_token") or "0.001")
        enriched["payment_authorization"] = authorization
        enriched.setdefault("local_only", False)
        enriched["credit"] = {
            **(enriched.get("credit") if isinstance(enriched.get("credit"), dict) else {}),
            "known": False,
            "wallet_address": str(context.get("wallet_address") or ""),
            "credit_ready": False,
            "estimated_max_credits": int(context.get("estimated_max_credits") or 1),
            "estimated_max_credits_approx": str(context.get("estimated_max_credits_approx") or ""),
            "approximation_only": True,
            "no_credit_hold_created": True,
            "no_credit_spent": True,
        }
        return enriched, context

    def _chat_console_readiness_check(self, key: str, title: str, ok: bool | None, detail: str) -> dict[str, Any]:
        return {
            "key": key,
            "title": title,
            "ok": ok,
            "detail": detail,
            "unknownText": "Not checked",
        }

    def _chat_console_backend_hub_readiness(self, body: dict[str, Any]) -> dict[str, Any]:
        enriched, context = self._chat_console_enrich_remote_overflow_body_with_backend_context(body)
        checks: list[dict[str, Any]] = []
        required_credits = int(context.get("estimated_max_credits") or 1)
        hub_response: dict[str, Any] | None = None
        hub_error = ""
        try:
            gateway = RemoteHubExecutionGateway(config=getattr(self.server, "config", None))
            provider = gateway._provider_for_request(enriched)
            hub_response = provider.validate_multisession_key(enriched)
            hub_reachable = bool(hub_response.get("hub_reachable", True) and hub_response.get("ok", True) is not False)
        except Exception as exc:
            hub_reachable = False
            hub_error = str(exc)

        checks.append(
            self._chat_console_readiness_check(
                "hub-reachability",
                "Hub reachable",
                hub_reachable,
                "Hub readiness can be checked." if hub_reachable else f"Could not contact the configured Hub: {hub_error or 'unreachable'}",
            )
        )
        checks.append(
            self._chat_console_readiness_check(
                "paid-overflow-setting",
                "Paid overflow setting",
                bool(context.get("remote_enabled")),
                "Enabled in Worker buyer settings." if context.get("remote_enabled") else "Disabled. The local backend will not submit a paid Hub request.",
            )
        )
        wallet_address = str(context.get("wallet_address") or "")
        checks.append(
            self._chat_console_readiness_check(
                "connected-wallet",
                "Connected wallet",
                bool(wallet_address),
                f"Wallet: {wallet_address}" if wallet_address else "No backend-known wallet-backed multi-session key is available.",
            )
        )
        key_id = str(context.get("multisession_key_id") or "")
        hub_valid = bool(hub_response.get("valid")) if isinstance(hub_response, dict) else False
        checks.append(
            self._chat_console_readiness_check(
                "hub-key-validation",
                "Multi-session key usable",
                hub_valid,
                (
                    "Hub confirms this key is active for the wallet."
                    if hub_valid
                    else (
                        str(hub_response.get("user_message") or "The Hub did not accept the active multi-session key.")
                        if isinstance(hub_response, dict)
                        else "The active multi-session key could not be checked with Hub."
                    )
                ),
            )
        )
        account = hub_response.get("account") if isinstance(hub_response, dict) and isinstance(hub_response.get("account"), dict) else {}
        available_credits = int(account.get("available_credits", 0) or 0)
        funds_ok = available_credits >= required_credits
        checks.append(
            self._chat_console_readiness_check(
                "spendable-credits",
                "Spendable bridged credits",
                funds_ok,
                f"Hub sees available {available_credits}; approximate whole-credit authorization requires {required_credits}.",
            )
        )
        budget_ok = bool(context.get("estimate_ok")) and funds_ok
        checks.append(
            self._chat_console_readiness_check(
                "authorization-budget",
                "Approximate authorization",
                budget_ok,
                (
                    f"Approximate budget is {context.get('estimated_max_credits_approx') or required_credits} credits before whole-credit rounding; holding at most {required_credits}."
                    if context.get("estimate_ok")
                    else "This request could not be estimated safely."
                ),
            )
        )

        ready = bool(
            hub_reachable
            and context.get("remote_enabled")
            and wallet_address
            and key_id
            and hub_valid
            and funds_ok
            and context.get("estimate_ok")
        )
        reason_code = "paid_overflow_ready" if ready else "paid_overflow_not_ready"
        user_message = "Paid overflow is ready. Hub accepts the key and sees enough spendable bridged credits."
        if not ready:
            failing = next((item for item in checks if item.get("ok") is False), None)
            reason_code = (
                str((hub_response or {}).get("reason_code") or "")
                if isinstance(hub_response, dict) and not hub_valid
                else str((failing or {}).get("key") or "paid_overflow_not_ready")
            )
            user_message = str((failing or {}).get("detail") or "Paid overflow readiness did not pass.")

        return {
            "ok": True,
            "ready": ready,
            "valid": hub_valid,
            "hub_reachable": hub_reachable,
            "reason_code": reason_code,
            "user_message": user_message,
            "paid_overflow_enabled": bool(context.get("remote_enabled")),
            "wallet_address": wallet_address,
            "account_id": str((hub_response or {}).get("account_id") or ""),
            "multisession_key_id": key_id,
            "chain_id": str(context.get("chain_id") or ""),
            "account": account,
            "available_credits": available_credits,
            "required_credits": required_credits,
            "credit_ready": funds_ok,
            "funds_ok": funds_ok,
            "max_output_tokens": int(context.get("max_output_tokens") or 1024),
            "credits_per_token": str(context.get("credits_per_token") or "0.001"),
            "estimated_input_tokens": int(context.get("estimated_input_tokens") or 0),
            "estimated_max_credits": int(context.get("estimated_max_credits") or 1),
            "estimated_max_credits_approx": str(context.get("estimated_max_credits_approx") or ""),
            "checks": checks,
            "hub": hub_response or None,
            "hub_error": hub_error,
        }

    def _chat_console_remote_overflow_engine(self) -> RemoteOverflowDecisionEngine:
        def local_capacity_provider(*, thread_id: str = "", max_local_concurrency: int = 1) -> dict[str, Any]:
            return self.server.chat_ai_processes.local_ai_capacity_snapshot(
                thread_id=thread_id,
                max_local_concurrency=max_local_concurrency,
            )

        return RemoteOverflowDecisionEngine(local_capacity_provider=local_capacity_provider)

    def _handle_chat_console_remote_overflow_assess(self) -> None:
        try:
            body = self._read_json()
            if not isinstance(body, dict):
                raise ValueError("Remote overflow assessment requires a JSON object.")
            enriched_body, _context = self._chat_console_enrich_remote_overflow_body_with_backend_context(body)
            assessment = self._chat_console_remote_overflow_engine().assess(enriched_body)
            self.server.signal(
                "api-chat-console-remote-overflow-assess",
                action=assessment.action,
                reason_code=assessment.reason_code,
                authorization_required=assessment.authorization_required,
            )
            self._send_json({"ok": True, "remote_overflow": assessment.as_dict()})
        except Exception as exc:
            self.server.signal("api-chat-console-remote-overflow-assess-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_chat_console_remote_overflow_hub_readiness(self) -> None:
        try:
            body = self._read_json()
            if not isinstance(body, dict):
                raise ValueError("Remote Hub readiness requires a JSON object.")

            readiness = self._chat_console_backend_hub_readiness(body)
            self.server.signal(
                "api-chat-console-remote-overflow-hub-readiness",
                valid=bool(readiness.get("valid")),
                ready=bool(readiness.get("ready")),
                hub_reachable=bool(readiness.get("hub_reachable")),
                reason_code=str(readiness.get("reason_code") or ""),
            )
            self._send_json({"ok": True, "hub_readiness": readiness})
        except Exception as exc:
            self.server.signal("api-chat-console-remote-overflow-hub-readiness-error", error=exc)
            self._send_json(
                {
                    "ok": True,
                    "hub_readiness": {
                        "ok": False,
                        "valid": False,
                        "ready": False,
                        "hub_reachable": False,
                        "reason_code": "hub_unreachable",
                        "user_message": f"Hub readiness could not be checked: {exc}",
                    },
                }
            )

    def _chat_console_remote_hub_submit_body(self, body: dict[str, Any]) -> dict[str, Any]:
        """Normalize a user-selected Phase 6 Hub submit with backend-owned paid overflow context."""

        submit_body = dict(body)
        if not submit_body.get("remote_hub_current_request"):
            return submit_body

        submit_body, context = self._chat_console_enrich_remote_overflow_body_with_backend_context(submit_body)
        readiness = self._chat_console_backend_hub_readiness(submit_body)
        remote_enabled = bool(readiness.get("paid_overflow_enabled"))
        credit_ready = bool(readiness.get("ready") and readiness.get("credit_ready"))
        account = readiness.get("account") if isinstance(readiness.get("account"), dict) else {}
        submit_body["remote_overflow_enabled"] = remote_enabled
        submit_body["local_only"] = bool(submit_body.get("local_only", False))
        submit_body["authorization_granted_by_user"] = bool(submit_body.get("authorization_granted_by_user"))
        submit_body["credit_ready"] = credit_ready
        submit_body["bridged_credits"] = int(account.get("bridge_completed_credits", account.get("available_credits", 0)) or 0)
        submit_body["spendable_credits"] = int(account.get("available_credits", 0) or 0)
        submit_body["pending_holds"] = int(account.get("held_credits", 0) or 0)
        submit_body["willing_worker_count"] = 1 if credit_ready else 0

        credit_payload = submit_body.get("credit") if isinstance(submit_body.get("credit"), dict) else {}
        submit_body["credit"] = {
            **credit_payload,
            "known": bool(readiness.get("hub_reachable")),
            "wallet_address": str(readiness.get("wallet_address") or context.get("wallet_address") or ""),
            "account_id": str(readiness.get("account_id") or ""),
            "credit_ready": credit_ready,
            "bridged_credits": int(submit_body.get("bridged_credits") or 0),
            "spendable_credits": int(submit_body.get("spendable_credits") or 0),
            "pending_holds": int(submit_body.get("pending_holds") or 0),
            "estimated_max_credits": int(readiness.get("estimated_max_credits") or context.get("estimated_max_credits") or 1),
            "estimated_max_credits_approx": str(readiness.get("estimated_max_credits_approx") or context.get("estimated_max_credits_approx") or ""),
            "approximation_only": True,
            "no_credit_hold_created": True,
            "no_credit_spent": True,
        }

        preflight_seed = str(
            submit_body.get("pending_request_id")
            or submit_body.get("run_id")
            or submit_body.get("thread_id")
            or "request"
        ).replace(" ", "_")[:96]
        hub_payload = submit_body.get("hub") if isinstance(submit_body.get("hub"), dict) else {}
        submit_body["hub"] = {
            **hub_payload,
            "mode": "remote_hub_current_request",
            "willing_worker_count": 1 if credit_ready else 0,
            "preflight_id": str(hub_payload.get("preflight_id") or f"phase6-remote-hub-{preflight_seed}"),
            "real_remote_worker_contacted": False,
            "private_worker_prices_exposed": False,
            "readiness": readiness,
        }
        return submit_body

    def _chat_console_remote_hub_submit_engine(self, body: dict[str, Any]) -> RemoteOverflowDecisionEngine:
        if not body.get("remote_hub_current_request"):
            return self._chat_console_remote_overflow_engine()

        def local_capacity_provider(*, thread_id: str = "", max_local_concurrency: int = 1) -> dict[str, Any]:
            snapshot = body.get("local_capacity_snapshot") if isinstance(body.get("local_capacity_snapshot"), dict) else {}
            if snapshot and not snapshot.get("available_now"):
                current = dict(snapshot)
                current.setdefault("ok", True)
                current.setdefault("busy", True)
                current.setdefault("available_now", False)
                current.setdefault("thread_id", thread_id)
                current.setdefault("max_local_concurrency", max_local_concurrency)
                current.setdefault("reason_code", current.get("reason_code") or "remote_hub_user_selected")
                current.setdefault("user_message", current.get("user_message") or "The user selected Remote Hub for this pending request.")
                return current
            return {
                "ok": True,
                "available_now": False,
                "busy": True,
                "reason_code": "remote_hub_user_selected",
                "user_message": "The user selected Remote Hub for this pending request.",
                "thread_id": thread_id,
                "active_run_count": max(1, int(body.get("active_run_count") or 1)),
                "max_local_concurrency": max_local_concurrency,
                "active_thread_ids": [thread_id] if thread_id else [],
                "active_runs": [],
                "cards": [
                    {
                        "key": "local_capacity",
                        "title": "Local AI capacity",
                        "status": "blocked",
                        "message": "The user selected Remote Hub for this pending request.",
                        "details": {
                            "reason_code": "remote_hub_user_selected",
                            "checked_thread_id": thread_id,
                            "max_local_concurrency": max_local_concurrency,
                        },
                    }
                ],
            }

        return RemoteOverflowDecisionEngine(local_capacity_provider=local_capacity_provider)

    def _handle_chat_console_remote_overflow_hub_submit(self, *, compatibility_alias: bool = False) -> None:
        try:
            body = self._read_json()
            if not isinstance(body, dict):
                raise ValueError("Remote Hub overflow submit requires a JSON object.")

            submit_body = self._chat_console_remote_hub_submit_body(body)
            engine = self._chat_console_remote_hub_submit_engine(submit_body)
            assessment = engine.assess(submit_body)
            result = RemoteHubExecutionGateway(config=getattr(self.server, "config", None)).run(submit_body, assessment)
            remote_result = result.get("remote_overflow_result") if isinstance(result.get("remote_overflow_result"), dict) else {}
            remote_overflow_request_id = str(remote_result.get("remote_overflow_request_id") or "")
            self.server.signal(
                "api-chat-console-remote-overflow-hub-submit",
                status=result.get("status"),
                reason_code=assessment.reason_code,
                remote_overflow_request_id=remote_overflow_request_id,
                compatibility_alias=bool(compatibility_alias),
                safe_remote_hub_path=bool(remote_result.get("safe_remote_hub_path")),
            )

            response_payload = remote_result.get("response") if isinstance(remote_result.get("response"), dict) else {}
            cell = submit_body.get("cell") if isinstance(submit_body.get("cell"), dict) else {}
            if result.get("ok") and cell:
                response = ChatResponse(
                    content=str(response_payload.get("content") or ""),
                    provider=str(response_payload.get("provider") or "remote-hub-ai"),
                    model=str(response_payload.get("model") or submit_body.get("model") or "remote-hub-ai"),
                    metadata=response_payload.get("metadata") if isinstance(response_payload.get("metadata"), dict) else {},
                )
                output_cell = build_output_cell(cell, ai_response_to_parts(response), status="ok", provider=response.provider, model=response.model)
                output_cell.setdefault("metadata", {})
                output_cell["metadata"] = {
                    **(output_cell.get("metadata") if isinstance(output_cell.get("metadata"), dict) else {}),
                    "remote_overflow": True,
                    "remote_hub_ai": True,
                    "remote_hub_gateway": True,
                    "safe_remote_hub_path": True,
                    "remote_overflow_request_id": remote_overflow_request_id,
                    "run_id": str(submit_body.get("run_id") or ""),
                    "thread_id": str(submit_body.get("thread_id") or submit_body.get("chat_thread_id") or ""),
                    "credit_hold_created": bool((response.metadata.get("payment") or {}).get("hold_id")) if isinstance(response.metadata.get("payment"), dict) else False,
                    "credit_spent": bool((response.metadata.get("payment") or {}).get("charged_credits")) if isinstance(response.metadata.get("payment"), dict) else False,
                    "no_credit_hold_created": not bool((response.metadata.get("payment") or {}).get("hold_id")) if isinstance(response.metadata.get("payment"), dict) else True,
                    "no_credit_spent": not bool((response.metadata.get("payment") or {}).get("charged_credits")) if isinstance(response.metadata.get("payment"), dict) else True,
                }
                result["output_cell"] = output_cell

            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.CONFLICT
            self._send_json(result, status=status)
        except Exception as exc:
            self.server.signal("api-chat-console-remote-overflow-hub-submit-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_chat_console_remote_overflow_mock_submit(self) -> None:
        self._handle_chat_console_remote_overflow_hub_submit(compatibility_alias=True)


    def _chat_console_preview(self, value: Any, *, limit: int = 700) -> str:
        text = " ".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").split())
        if len(text) > limit:
            return text[: max(0, limit - 1)].rstrip() + "…"
        return text

    def _chat_console_message_history_payload(self, messages: list[ChatMessage]) -> dict[str, Any]:
        system_prompts: list[str] = []
        user_prompts: list[str] = []
        previews: list[str] = []
        for index, message in enumerate(messages):
            role = str(getattr(message, "role", "") or "").strip() or "message"
            content = str(getattr(message, "content", "") or "")
            preview = self._chat_console_preview(content, limit=500)
            if preview:
                previews.append(f"{index + 1}:{role}: {preview}")
            if role == "system" and content:
                system_prompts.append(content)
            elif role == "user" and content:
                user_prompts.append(content)
        return {
            "message_count": len(messages),
            "system_prompt_preview": self._chat_console_preview("\n\n".join(system_prompts), limit=900),
            "user_prompt_preview": self._chat_console_preview("\n\n".join(user_prompts[-2:]), limit=700),
            "input_messages_preview": " | ".join(previews[:6]),
            "system_prompt_chars": sum(len(item) for item in system_prompts),
            "user_prompt_chars": sum(len(item) for item in user_prompts),
        }

    def _chat_console_stream_callback(self, *, run_id: str, log_path: Path | None = None):
        def on_stream(event: dict[str, Any]) -> None:
            event_type = str(event.get("type") or "")
            provider = str(event.get("provider") or getattr(getattr(self.server.computer, "provider", None), "name", ""))
            model = str(event.get("model") or getattr(getattr(self.server.computer, "provider", None), "model", ""))
            if event_type == "content_delta":
                latest_text = " ".join(str(event.get("content_preview") or event.get("delta") or "").split())
                title = "Model text transmitted"
                message = latest_text[:500]
            elif event_type == "thinking_delta":
                latest_text = str(event.get("thinking_preview") or event.get("delta") or "")
                title = "Model thinking transmitted"
                message = latest_text[:500]
            elif event_type == "request_submitted":
                latest_text = ""
                title = "Model request submitted"
                message = str(event.get("status_preview") or "Provider request submitted; waiting for first response.")
            elif event_type == "response_opened":
                latest_text = ""
                title = "Model response opened"
                message = str(event.get("status_preview") or "Provider response opened; waiting for streamed tokens.")
            elif event_type == "request_waiting":
                latest_text = ""
                title = "Model request still waiting"
                message = str(event.get("status_preview") or "Provider request is still waiting for a response.")
            elif event_type == "response_waiting":
                latest_text = ""
                title = "Model response still waiting"
                message = str(event.get("status_preview") or "Provider response is open; waiting for streamed tokens.")
            else:
                latest_text = ""
                title = "Model stream status"
                message = str(event.get("status_preview") or event_type or "Model stream status updated.")
            self.server.activity.record(
                source="chat-console",
                kind="ai",
                time_model="parallel",
                severity="info",
                title=title,
                message=message,
                status="running",
                tags=["ai", "local-ai", "chat-console", "model-call", "stream", "thinking"],
                data={
                    "run_id": run_id,
                    "activity_filter": "ai",
                    "provider": provider,
                    "model": model,
                    "log_file": str(log_path) if log_path else "",
                    "latest_text": latest_text[:500],
                    "thinking_preview": latest_text[:500] if event_type == "thinking_delta" else "",
                    "status_preview": message if event_type not in {"content_delta", "thinking_delta"} else "",
                    "content_chars": event.get("content_chars", 0),
                    "thinking_chars": event.get("thinking_chars", 0),
                    "elapsed_ms": event.get("elapsed_ms"),
                    "think": event.get("think"),
                    "thinking_enabled": event.get("thinking_enabled"),
                    "thinking_state": event.get("thinking_state"),
                    "thinking_status": event.get("thinking_status"),
                    "stream_event_type": event.get("stream_event_type") or event_type,
                    "stream_phase": event.get("stream_phase"),
                    "stream_heartbeat": event.get("stream_heartbeat"),
                    "waiting_reason": event.get("waiting_reason"),
                    "transport": event.get("transport"),
                    "provider_class": event.get("provider_class"),
                    "base_url": event.get("base_url"),
                    "stream": event.get("stream"),
                    "options_keys": event.get("options_keys"),
                    "message_count": event.get("message_count"),
                    "request_bytes": event.get("request_bytes"),
                    "timeout_s": event.get("timeout_s"),
                    "diagnostic_label": event.get("diagnostic_label"),
                    "diagnostic_run_id": event.get("diagnostic_run_id"),
                    "raw_thinking_exposed": bool(event.get("raw_thinking_exposed")) if "raw_thinking_exposed" in event else False,
                    "running_text": (
                        "model text streaming"
                        if event_type == "content_delta"
                        else latest_text[:500]
                        if event_type == "thinking_delta"
                        else message
                    ),
                    "history_label": f"model stream: {latest_text[:500]}" if event_type == "content_delta" else f"model thinking: {latest_text[:500]}" if event_type == "thinking_delta" else message,
                    "rag_type": "model_stream",
                },
            )
            self._append_chat_console_session_log(
                log_path,
                {
                    "event": "stream",
                    "run_id": run_id,
                    "stream_type": event_type,
                    "provider": provider,
                    "model": model,
                    "latest_text": latest_text[:1000],
                    "thinking_preview": latest_text[:1000] if event_type == "thinking_delta" else "",
                    "status_preview": message if event_type not in {"content_delta", "thinking_delta"} else "",
                    "content_chars": event.get("content_chars", 0),
                    "thinking_chars": event.get("thinking_chars", 0),
                    "elapsed_ms": event.get("elapsed_ms"),
                    "think": event.get("think"),
                    "thinking_enabled": event.get("thinking_enabled"),
                    "thinking_state": event.get("thinking_state"),
                    "thinking_status": event.get("thinking_status"),
                    "stream_event_type": event.get("stream_event_type") or event_type,
                    "stream_phase": event.get("stream_phase"),
                    "stream_heartbeat": event.get("stream_heartbeat"),
                    "waiting_reason": event.get("waiting_reason"),
                    "transport": event.get("transport"),
                    "provider_class": event.get("provider_class"),
                    "base_url": event.get("base_url"),
                    "stream": event.get("stream"),
                    "options_keys": event.get("options_keys"),
                    "message_count": event.get("message_count"),
                    "request_bytes": event.get("request_bytes"),
                    "timeout_s": event.get("timeout_s"),
                    "diagnostic_label": event.get("diagnostic_label"),
                    "diagnostic_run_id": event.get("diagnostic_run_id"),
                    "raw_thinking_exposed": bool(event.get("raw_thinking_exposed")) if "raw_thinking_exposed" in event else False,
                },
            )

        return on_stream

    def _handle_chat_console_cell_evaluate(self) -> None:
        try:
            body = self._read_json()
            cell = body.get("cell") if isinstance(body.get("cell"), dict) else {}
            if not isinstance(cell, dict):
                raise ValueError("Cell payload is required.")
            cell_type, source = validate_evaluation_cell(cell)
            self.server.signal("api-chat-console-cell-evaluate", cell_type=cell_type, source_chars=len(source))
            if cell_type == "ai":
                run_id = str(body.get("run_id") or cell.get("run_id") or f"chat_ai_{int(time.time() * 1000)}").strip()
                thread_id = self._chat_console_thread_id(body, cell)
                log_path = self._chat_console_session_log_path(run_id)
                attachments = self._chat_console_evaluation_attachments(cell.get("attachments") if isinstance(cell.get("attachments"), list) else [])
                append_text_log(
                    log_path,
                    "route accepted chat console AI request",
                    run_id=run_id,
                    thread_id=thread_id,
                    source_chars=len(source),
                    source=source,
                    cell=cell,
                    attachments=attachments,
                    provider=getattr(getattr(self.server.computer, "provider", None), "name", ""),
                    model=getattr(getattr(self.server.computer, "provider", None), "model", ""),
                )
                self.server.activity.record(
                    source="chat-console",
                    kind="ai",
                    time_model="parallel",
                    severity="info",
                    title="AI notebook request queued",
                    message=source[:500],
                    status="running",
                    tags=["ai", "local-ai", "chat-console", "model-call", "subprocess"],
                    data={
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "activity_filter": "ai",
                        "provider": getattr(getattr(self.server.computer, "provider", None), "name", ""),
                        "model": getattr(getattr(self.server.computer, "provider", None), "model", ""),
                        "log_file": str(log_path),
                        "raw_thinking_exposed": False,
                        "running_text": "AI notebook subprocess queued",
                        "rag_type": "chat_console_ai",
                    },
                )
                if _should_inline_test_provider(getattr(getattr(self.server, "computer", None), "provider", None)):
                    append_text_log(
                        log_path,
                        "using inline non-production provider instead of subprocess",
                        provider_class=f"{self.server.computer.provider.__class__.__module__}.{self.server.computer.provider.__class__.__name__}",
                        reason="test provider objects cannot be reconstructed safely in a child process",
                    )
                    if hasattr(self.server.computer, "chat_console_ai"):
                        inline_response = self.server.computer.chat_console_ai(source, attachments=attachments)
                    else:
                        inline_response = self.server.computer.chat(source)
                    payload = {
                        "response": {
                            "content": inline_response.content,
                            "provider": inline_response.provider,
                            "model": inline_response.model,
                            "metadata": inline_response.metadata,
                        }
                    }
                else:
                    payload = self.server.chat_ai_processes.run(
                        command={
                            "mode": "chat_console_ai",
                            "run_id": run_id,
                            "source": source,
                            "attachments": attachments,
                            "config": config_to_payload(self.server.config),
                        },
                        thread_id=thread_id,
                        log_file=log_path,
                        activity_bus=self.server.activity,
                        cwd=self.server.debug_root,
                        max_local_concurrency=1,
                    )
                response_payload = payload.get("response") if isinstance(payload.get("response"), dict) else {}
                response = ChatResponse(
                    content=str(response_payload.get("content") or ""),
                    provider=str(response_payload.get("provider") or getattr(getattr(self.server.computer, "provider", None), "name", "")),
                    model=str(response_payload.get("model") or getattr(getattr(self.server.computer, "provider", None), "model", "")),
                    metadata=response_payload.get("metadata") if isinstance(response_payload.get("metadata"), dict) else {},
                )
                output_cell = build_output_cell(cell, ai_response_to_parts(response), status="ok", provider=response.provider, model=response.model)
                output_cell.setdefault("metadata", {})
                output_cell["metadata"] = {
                    **(output_cell.get("metadata") if isinstance(output_cell.get("metadata"), dict) else {}),
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "activity_filter": "ai",
                    "log_file": str(log_path),
                    "subprocess": True,
                }
                append_text_log(
                    log_path,
                    "route completed chat console AI request",
                    run_id=run_id,
                    thread_id=thread_id,
                    response_chars=len(response.content),
                    provider=response.provider,
                    model=response.model,
                )
                response_json = {"ok": True, "status": "completed", "output_cell": output_cell, "run_id": run_id, "thread_id": thread_id, "log_file": str(log_path)}
                self.server.chat_ai_processes.remember_route_result(run_id=run_id, payload=response_json)
                self._send_json(response_json)
                return
            if cell_type == "mathics":
                timeout_s = max(1.0, min(30.0, float(cell.get("timeout_s", body.get("timeout_s", 10)) or 10)))
                result = evaluate_mathics_expression(source, timeout_s=timeout_s)
                status = "ok" if result.get("ok") else "error"
                output_cell = build_output_cell(cell, mathics_result_to_parts(result, source), status=status)
                self._send_json({"ok": True, "output_cell": output_cell})
                return
            if cell_type in {"javascript", "python", "basic"}:
                raise ValueError(f"{cell_type.capitalize()} chat cells run in the browser code runtime.")
            if cell_type == "terminal":
                command = source
                if len(command) > 4000:
                    raise ValueError("Terminal command is limited to 4000 characters.")
                cwd = self._terminal_cwd(str(cell.get("cwd", body.get("cwd", ".")) or "."))
                timeout_s = max(1.0, min(120.0, float(cell.get("timeout_s", body.get("timeout_s", 30)) or 30)))
                started = time.monotonic()
                completed = subprocess.run(
                    ["powershell", "-NoLogo", "-NoProfile", "-Command", command],
                    cwd=str(cwd),
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
                result = {
                    "command": command,
                    "cwd": str(cwd),
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "timed_out": False,
                }
                status = "ok" if completed.returncode == 0 else "error"
                output_cell = build_output_cell(cell, terminal_result_to_parts(result), status=status)
                self._send_json({"ok": True, "output_cell": output_cell})
                return
        except subprocess.TimeoutExpired as exc:
            result = {
                "command": str((body.get("cell") or {}).get("source", "")) if "body" in locals() else "",
                "cwd": str(cwd) if "cwd" in locals() else "",
                "exit_code": None,
                "stdout": exc.stdout if isinstance(exc.stdout, str) else "",
                "stderr": exc.stderr if isinstance(exc.stderr, str) else "",
                "duration_ms": int((time.monotonic() - started) * 1000) if "started" in locals() else 0,
                "timed_out": True,
            }
            output_cell = build_output_cell(cell if "cell" in locals() and isinstance(cell, dict) else {}, terminal_result_to_parts(result), status="error")
            self._send_json({"ok": True, "output_cell": output_cell})
        except ChatAISubprocessBusy as exc:
            self.server.signal("api-chat-console-cell-evaluate-busy", error=exc)
            self._send_json({"ok": False, "error": str(exc), "busy": True}, status=HTTPStatus.CONFLICT)
        except ChatAISubprocessCancelled as exc:
            self.server.signal("api-chat-console-cell-evaluate-cancelled", error=exc)
            self._send_json({"ok": False, "error": str(exc), "cancelled": True}, status=HTTPStatus.BAD_REQUEST)
        except ChatAISubprocessError as exc:
            self.server.signal("api-chat-console-cell-evaluate-subprocess-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-chat-console-cell-evaluate-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
