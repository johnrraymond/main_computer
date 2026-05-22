from __future__ import annotations

from main_computer.viewport_state import *  # noqa: F401,F403

class ViewportAiderRoutesMixin:
    def _aider_context_status_payload(self) -> dict[str, Any]:
        status = self.server.aider_web_context.status()
        activities = self.server.aider_jobs.status()
        status["activities"] = activities
        by_archive: dict[str, list[dict[str, Any]]] = {}
        for activity in activities:
            archive_id = str(activity.get("archive_id") or "").strip()
            if archive_id:
                by_archive.setdefault(archive_id, []).append(activity)
        active = status.get("active")
        if isinstance(active, dict):
            active["activities"] = by_archive.get(str(active.get("archive_id") or "").strip(), [])
        for archive in status.get("archives", []):
            if isinstance(archive, dict):
                archive["activities"] = by_archive.get(str(archive.get("id") or "").strip(), [])
        current = status.get("current_archive")
        if isinstance(current, dict):
            current["activities"] = by_archive.get(str(current.get("id") or "").strip(), [])
        return status

    def _handle_aider_context_status(self) -> None:
        try:
            self.server.signal("api-aider-context-status")
            self._send_json({"ok": True, **self._aider_context_status_payload()})
        except Exception as exc:
            self.server.signal("api-aider-context-status-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_aider_jobs_status(self) -> None:
        try:
            self.server.signal("api-aider-jobs-status")
            self._send_json({"ok": True, "activities": self.server.aider_jobs.status()})
        except Exception as exc:
            self.server.signal("api-aider-jobs-status-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_aider_context_archive(self) -> None:
        try:
            body = self._read_json()
            repo_dir = str(body.get("repo_dir", ".") or ".").strip() or "."
            files = parse_file_list(body.get("files", []))
            self.server.signal("api-aider-context-archive", repo=repo_dir, files=len(files))
            result = self.server.aider_web_context.archive_active()
            refreshed = self.server.aider_web_context.reset_active(repo_dir=repo_dir, files=files)
            new_archive_id = str(result.get("active", {}).get("archive_id") or "").strip()
            if new_archive_id and isinstance(refreshed.get("active"), dict):
                refreshed["active"]["archive_id"] = new_archive_id
            result["active"] = refreshed["active"]
            result["archive_count"] = refreshed["archive_count"]
            result.update(self._aider_context_status_payload())
            if new_archive_id and isinstance(result.get("active"), dict):
                result["active"]["archive_id"] = new_archive_id
            self._send_json(result)
        except Exception as exc:
            self.server.signal("api-aider-context-archive-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_aider_context_load(self) -> None:
        try:
            body = self._read_json()
            archive_id = str(body.get("archive_id", "") or "").strip()
            if not archive_id:
                raise ValueError("Archive id is required.")
            self.server.signal("api-aider-context-load", archive_id=archive_id)
            result = self.server.aider_web_context.load_archive(archive_id)
            result.update(self._aider_context_status_payload())
            self._send_json(result)
        except Exception as exc:
            self.server.signal("api-aider-context-load-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_aider_context_reset(self) -> None:
        try:
            body = self._read_json()
            repo_dir = str(body.get("repo_dir", ".") or ".").strip() or "."
            files = parse_file_list(body.get("files", []))
            self.server.signal("api-aider-context-reset", repo=repo_dir, files=len(files))
            result = self.server.aider_web_context.reset_active(repo_dir=repo_dir, files=files)
            result.update(self._aider_context_status_payload())
            self._send_json(result)
        except Exception as exc:
            self.server.signal("api-aider-context-reset-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _aider_request_with_archive_history(
        self,
        request: AiderActionRequest,
        *,
        promote: bool = False,
    ) -> tuple[AiderActionRequest, dict[str, str]]:
        history = self.server.aider_web_context.prepare_aider_history_files(promote=promote)
        updated = replace(
            request,
            chat_history_file=history.get("chat_history_file"),
            input_history_file=history.get("input_history_file"),
        )
        return updated, history

    def _handle_aider_prepare(self) -> None:
        try:
            body = self._read_json()
            request = self._aider_action_request(body)
            request, aider_history = self._aider_request_with_archive_history(request, promote=False)
            self.server.signal(
                "api-aider-prepare",
                repo=request.repo_dir,
                files=len(request.files),
                dry_run=request.dry_run,
                timeout_s=request.timeout_seconds or self.server.aider_config.timeout_seconds,
                fallback=request.fallback,
            )
            result = prepare_aider_action(request, self.server.aider_config)
            self._write_aider_log(
                "prepare",
                repo_dir=result.repo_dir,
                git_root=result.git_root,
                files=request.files,
                instruction=request.instruction,
                model=request.model or self.server.aider_config.default_model,
                dry_run=request.dry_run,
                timeout_seconds=result.timeout_seconds,
                fallback=request.fallback,
                command=result.command,
                ok=result.ok,
                aider_history=aider_history,
            )
            self._append_aider_context_entry(
                kind="prepare",
                repo_dir=result.repo_dir,
                files=request.files,
                instruction=request.instruction,
                dry_run=request.dry_run,
                ok=result.ok,
                route="/api/applications/aider/prepare",
                result_excerpt=self._aider_prepare_excerpt(request, result),
                metadata={"command": result.command, "aider_history": aider_history},
            )
            self._send_json(asdict(result))
        except AiderValidationError as exc:
            self.server.signal("api-aider-prepare-rejected", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-aider-prepare-error", error=exc)
            self._write_aider_log("prepare_error", error=str(exc))
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_aider_run(self) -> None:
        try:
            body = self._read_json()
            request = self._aider_action_request(body)
            request, aider_history = self._aider_request_with_archive_history(request, promote=True)
            prepared = prepare_aider_action(request, self.server.aider_config)
            self.server.signal(
                "api-aider-run-accepted",
                repo=prepared.repo_dir,
                archive_id=aider_history.get("archive_id"),
                files=len(request.files),
                dry_run=request.dry_run,
                timeout_s=prepared.timeout_seconds,
                fallback=request.fallback,
            )
            job = self.server.aider_jobs.start_run(
                request=request,
                aider_history=aider_history,
                prepared=prepared,
            )
            payload = {
                "ok": True,
                "accepted": True,
                "job": job,
                "message": "Aider run started in the backend.",
                **self._aider_context_status_payload(),
            }
            self._send_json(payload, status=HTTPStatus.ACCEPTED)
        except AiderValidationError as exc:
            self.server.signal("api-aider-run-rejected", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-aider-run-error", error=exc)
            self._write_aider_log("run_error", error=str(exc))
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _aider_timeout_seconds(self, value: Any) -> int | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            timeout_seconds = int(text)
        except ValueError as exc:
            raise ValueError("Aider timeout must be a whole number of seconds.") from exc
        if timeout_seconds < 1:
            raise ValueError("Aider timeout must be at least 1 second.")
        return timeout_seconds

    def _aider_action_request(self, body: dict[str, Any]) -> AiderActionRequest:
        model = str(body.get("model", "") or "").strip() or None
        return AiderActionRequest(
            repo_dir=str(body.get("repo_dir", ".") or ".").strip() or ".",
            instruction=str(body.get("instruction", "") or "").strip(),
            files=parse_file_list(body.get("files", "")),
            model=model,
            dry_run=bool(body.get("dry_run", True)),
            extra_args=parse_file_list(body.get("extra_args", "")),
            timeout_seconds=self._aider_timeout_seconds(body.get("timeout_seconds")),
            fallback=bool(body.get("fallback", self.server.aider_config.fallback)),
        )

    def _write_aider_log(self, event: str, **fields: Any) -> None:
        debug_asset = self._write_aider_debug_artifact(event, **fields)
        if debug_asset:
            fields = {**fields, "debug_asset": debug_asset}
        try:
            append_aider_log(self.server.debug_root / "aider.log", event, **fields)
        except Exception as exc:
            self.server.signal("aider-log-error", event=event, error=exc)

    def _append_aider_context_entry(
        self,
        *,
        kind: str,
        repo_dir: str,
        files: list[str],
        instruction: str,
        dry_run: bool,
        ok: bool,
        route: str,
        returncode: int | None = None,
        duration_ms: int | None = None,
        result_excerpt: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.server.aider_web_context.append_entry(
                kind=kind,
                repo_dir=repo_dir,
                files=files,
                instruction=instruction,
                dry_run=dry_run,
                ok=ok,
                returncode=returncode,
                duration_ms=duration_ms,
                result_excerpt=self._log_excerpt(result_excerpt, limit=1200),
                route=route,
                metadata=metadata,
            )
        except Exception as exc:
            self.server.signal("aider-context-entry-error", kind=kind, error=exc)

    def _aider_prepare_excerpt(self, request: AiderActionRequest, result: Any) -> str:
        action = "Prepared dry-run Aider command preview." if request.dry_run else "Prepared Aider command preview."
        selected = len(request.files)
        if selected:
            return f"{action} Selected files: {selected}."
        return action

    def _aider_run_excerpt(self, request: AiderActionRequest, result: Any) -> str:
        if result.stdout.strip():
            return result.stdout
        if result.stderr.strip():
            return result.stderr
        if result.error:
            return result.error
        if result.ok and request.dry_run:
            return "Dry run completed. No changes were applied."
        if result.ok:
            return "Aider completed."
        return "Aider failed."
