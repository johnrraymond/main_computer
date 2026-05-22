from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

from main_computer.executor_models import ExecutorRequest
from main_computer.executor_tool_loop import ExecutorToolLoopConfig, run_executor_tool_loop
from main_computer.viewport_state import *  # noqa: F401,F403


class ViewportExecutorRoutesMixin:
    def _handle_executor_status(self) -> None:
        try:
            self.server.signal("api-executor-status")
            uploads = self.server.executor_backend.list_uploads(limit=50)
            tool_loop = {
                "enabled": self.server.config.executor_tool_loop_enabled,
                "auto_run": self.server.config.executor_ai_auto_run,
                "allow_network": self.server.config.executor_ai_allow_network,
                "max_steps": self.server.config.executor_ai_max_steps,
            }
            self._send_json({"ok": True, "executor": self.server.executor_backend.status(), "tool_loop": tool_loop, "uploads": uploads})
        except Exception as exc:
            self.server.signal("api-executor-status-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_executor_uploads_list(self) -> None:
        try:
            query = parse_qs(urlsplit(self.path).query)
            try:
                limit = int(query.get("limit", ["200"])[0])
            except (TypeError, ValueError):
                limit = 200
            limit = max(1, min(1000, limit))
            self.server.signal("api-executor-uploads-list", limit=limit)
            self._send_json({"ok": True, "uploads": self.server.executor_backend.list_uploads(limit=limit)})
        except Exception as exc:
            self.server.signal("api-executor-uploads-list-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_executor_upload_create(self) -> None:
        try:
            query = parse_qs(urlsplit(self.path).query)
            filename = (
                self.headers.get("X-Filename")
                or query.get("filename", ["upload.bin"])[0]
                or "upload.bin"
            )
            content_length = int(self.headers.get("Content-Length", "-1"))
            content_type = self.headers.get("Content-Type") or None
            if not content_type or content_type.startswith("multipart/"):
                # This endpoint is deliberately optimized for large raw binary uploads.
                # Frontends should POST the file body directly and pass ?filename=...
                content_type = "application/octet-stream"
            self.server.signal("api-executor-upload-start", filename=filename, content_length=content_length)
            record = self.server.executor_backend.save_upload(
                filename=filename,
                stream=self.rfile,
                content_length=content_length,
                mime_type=content_type,
            )
            self.server.signal("api-executor-upload-complete", upload_id=record.id, size=record.size)
            self._send_json({"ok": True, "upload": record.as_dict()})
        except Exception as exc:
            self.server.signal("api-executor-upload-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


    def _handle_executor_ai(self) -> None:
        try:
            if not self.server.config.executor_tool_loop_enabled:
                raise ValueError("Executor AI tool loop is disabled. Set MAIN_COMPUTER_EXECUTOR_TOOL_LOOP_ENABLED=1 to enable it.")
            body = self._read_json()
            prompt = str(body.get("prompt", "") or "").strip()
            if not prompt:
                raise ValueError("Prompt is required.")
            upload_ids = self._executor_ai_upload_ids(body)
            requested_steps = int(body.get("max_steps", self.server.config.executor_ai_max_steps) or self.server.config.executor_ai_max_steps)
            max_steps = max(1, min(self.server.config.executor_ai_max_steps, requested_steps))
            auto_run_requested = self._coerce_bool(body.get("auto_run"), default=self.server.config.executor_ai_auto_run)
            allow_network_requested = self._coerce_bool(body.get("allow_network"), default=self.server.config.executor_ai_allow_network)
            auto_run = bool(auto_run_requested and self.server.config.executor_ai_auto_run)
            allow_network = bool(allow_network_requested and self.server.config.executor_ai_allow_network)
            self.server.signal(
                "api-executor-ai-start",
                prompt_chars=len(prompt),
                uploads=len(upload_ids),
                max_steps=max_steps,
                auto_run=auto_run,
                allow_network=allow_network,
            )
            context_pack = self.server.computer.context_pack(prompt)
            result = run_executor_tool_loop(
                provider=self.server.computer.provider,
                prompt=prompt,
                context_text=context_pack.text,
                executor_backend=self.server.executor_backend,
                config=ExecutorToolLoopConfig(
                    max_steps=max_steps,
                    max_timeout_s=self.server.config.executor_timeout_s,
                    auto_run=auto_run,
                    allow_network=allow_network,
                ),
                upload_ids=upload_ids,
            )
            payload = result.as_dict()
            payload["policy"] = {
                "auto_run_requested": auto_run_requested,
                "auto_run_effective": auto_run,
                "auto_run_configured": self.server.config.executor_ai_auto_run,
                "allow_network_requested": allow_network_requested,
                "allow_network_effective": allow_network,
                "allow_network_configured": self.server.config.executor_ai_allow_network,
                "max_steps": max_steps,
                "configured_max_steps": self.server.config.executor_ai_max_steps,
            }
            payload["workspace_context"] = {
                "manifest_chars": context_pack.manifest_chars,
            }
            status = HTTPStatus.OK
            if result.status in {"invalid_tool_request", "invalid_executor_request", "blocked"}:
                status = HTTPStatus.BAD_REQUEST
            self.server.signal(
                "api-executor-ai-complete",
                ok=result.ok,
                status=result.status,
                steps=len(result.steps),
                needs_approval=result.needs_approval,
            )
            self._send_json(payload, status=status)
        except Exception as exc:
            self.server.signal("api-executor-ai-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _executor_ai_upload_ids(self, body: dict[str, Any]) -> list[str]:
        raw = body.get("upload_ids", body.get("input_ids", body.get("inputs", [])))
        if raw is None:
            return []
        raw_items = raw if isinstance(raw, (list, tuple, set)) else [raw]
        upload_ids: list[str] = []
        for item in raw_items:
            value = str(item or "").strip()
            if not value:
                continue
            if not re.fullmatch(r"upload_[a-f0-9]{16}", value):
                raise ValueError(f"Invalid upload id: {value}")
            if value not in upload_ids:
                upload_ids.append(value)
            if len(upload_ids) >= 64:
                break
        return upload_ids

    def _handle_executor_run(self) -> None:
        try:
            body = self._read_json()
            request = ExecutorRequest.from_mapping(
                body,
                max_timeout_s=self.server.config.executor_timeout_s,
            )
            self.server.signal(
                "api-executor-run-start",
                command_chars=len(request.command),
                cwd=request.cwd,
                timeout_s=request.timeout_s,
                network=request.network,
            )
            result = self.server.executor_backend.run(request)
            status = HTTPStatus.OK
            if result.timed_out:
                status = HTTPStatus.REQUEST_TIMEOUT
            elif not result.ok and result.error:
                status = HTTPStatus.BAD_REQUEST
            self.server.signal(
                "api-executor-run-complete",
                ok=result.ok,
                job_id=result.job_id,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                duration_ms=result.duration_ms,
                artifacts=len(result.artifacts),
            )
            self._send_json(result.as_dict(), status=status)
        except Exception as exc:
            self.server.signal("api-executor-run-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_executor_artifact_get(self) -> None:
        try:
            route_path = urlsplit(self.path).path
            prefix = "/api/executor/artifacts/"
            remainder = route_path[len(prefix):]
            job_id, _, relative = remainder.partition("/")
            if not job_id or not relative:
                raise FileNotFoundError("Artifact path is required.")
            artifact = self.server.executor_backend.artifact_path(job_id, unquote(relative))
            content_type = mimetypes.guess_type(artifact.name)[0] or "application/octet-stream"
            self.server.signal("api-executor-artifact-read", job_id=job_id, relative_path=relative)
            self._send_binary_file(artifact, content_type)
        except FileNotFoundError:
            self.server.signal("api-executor-artifact-not-found", path=self.path)
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.server.signal("api-executor-artifact-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _send_binary_file(self, path: Path, content_type: str) -> None:
        size = path.stat().st_size
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            with path.open("rb") as handle:
                shutil.copyfileobj(handle, self.wfile)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            self.server.signal("client-disconnected", path=self.path, error=exc)
