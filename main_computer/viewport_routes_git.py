from __future__ import annotations

from urllib.parse import parse_qs

from main_computer.viewport_state import *  # noqa: F401,F403

class ViewportGitRoutesMixin:
    def _send_git_operation_result(self, result: dict[str, Any]) -> None:
        status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
        if result.get("busy"):
            status = HTTPStatus.CONFLICT
        self._send_json(result, status=status)

    def _handle_git_operation_status(self) -> None:
        try:
            self.server.signal("api-git-operation-status")
            self._send_json(self.server.git_tools.git_operation_status())
        except Exception as exc:
            self.server.signal("api-git-operation-status-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_operation_cancel(self) -> None:
        try:
            self.server.signal("api-git-operation-cancel")
            self._send_json(self.server.git_tools.cancel_git_operation())
        except Exception as exc:
            self.server.signal("api-git-operation-cancel-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_status(self) -> None:
        try:
            body = self._read_json()
            repo_dir = str(body.get("repo_dir", ".") or ".")
            self.server.signal("api-git-status", repo_dir=repo_dir)
            self._send_json(self.server.git_tools.git_status(repo_dir))
        except Exception as exc:
            self.server.signal("api-git-status-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_projects(self) -> None:
        try:
            self.server.signal("api-git-projects")
            self._send_json(self.server.git_tools.git_projects())
        except Exception as exc:
            self.server.signal("api-git-projects-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_add(self) -> None:
        try:
            body = self._read_json()
            path = str(body.get("path", "") or "").strip()
            name = str(body.get("name", "") or "").strip()
            select = self._coerce_bool(body.get("select", True), default=True)
            self.server.signal("api-git-project-add", path=path)
            self._send_json(self.server.git_tools.add_git_project(path, name=name, select=select))
        except Exception as exc:
            self.server.signal("api-git-project-add-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_select(self) -> None:
        try:
            body = self._read_json()
            project_id = str(body.get("project_id", "") or "").strip()
            path = str(body.get("path", "") or "").strip()
            self.server.signal("api-git-project-select", project_id=project_id, path=path)
            self._send_json(self.server.git_tools.select_git_project(project_id=project_id, path=path))
        except Exception as exc:
            self.server.signal("api-git-project-select-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_archive(self) -> None:
        try:
            body = self._read_json()
            project_id = str(body.get("project_id", "") or "").strip()
            path = str(body.get("path", "") or "").strip()
            self.server.signal("api-git-project-archive", project_id=project_id, path=path)
            self._send_json(self.server.git_tools.archive_git_project(project_id=project_id, path=path))
        except Exception as exc:
            self.server.signal("api-git-project-archive-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_restore(self) -> None:
        try:
            body = self._read_json()
            project_id = str(body.get("project_id", "") or "").strip()
            path = str(body.get("path", "") or "").strip()
            select = self._coerce_bool(body.get("select", False), default=False)
            self.server.signal("api-git-project-restore", project_id=project_id, path=path)
            self._send_json(self.server.git_tools.restore_git_project(project_id=project_id, path=path, select=select))
        except Exception as exc:
            self.server.signal("api-git-project-restore-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_lock(self) -> None:
        try:
            body = self._read_json()
            project_id = str(body.get("project_id", "") or "").strip()
            path = str(body.get("path", "") or "").strip()
            locked = self._coerce_bool(body.get("locked", True), default=True)
            self.server.signal("api-git-project-lock", project_id=project_id, path=path, locked=locked)
            self._send_json(self.server.git_tools.lock_git_project(project_id=project_id, path=path, locked=locked))
        except Exception as exc:
            self.server.signal("api-git-project-lock-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_unlock(self) -> None:
        try:
            body = self._read_json()
            project_id = str(body.get("project_id", "") or "").strip()
            path = str(body.get("path", "") or "").strip()
            self.server.signal("api-git-project-unlock", project_id=project_id, path=path, locked=False)
            self._send_json(self.server.git_tools.lock_git_project(project_id=project_id, path=path, locked=False))
        except Exception as exc:
            self.server.signal("api-git-project-unlock-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_inspect(self) -> None:
        try:
            body = self._read_json()
            project_id = str(body.get("project_id", "") or "").strip()
            path = str(body.get("path", "") or "").strip()
            self.server.signal("api-git-project-inspect", project_id=project_id, path=path)
            self._send_json(self.server.git_tools.inspect_git_project(project_id=project_id, path=path))
        except Exception as exc:
            self.server.signal("api-git-project-inspect-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_archive_files_status(self) -> None:
        try:
            body = self._read_json()
            self.server.signal("api-git-project-archive-files-status")
            self._send_json(self.server.git_tools.git_project_archive_files_status(body))
        except Exception as exc:
            self.server.signal("api-git-project-archive-files-status-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_archive_files(self) -> None:
        try:
            body = self._read_json()
            self.server.signal("api-git-project-archive-files", dry_run=bool(body.get("dry_run", True)))
            result = self.server.git_tools.archive_git_project_files(body)
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
        except Exception as exc:
            self.server.signal("api-git-project-archive-files-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_action_run(self) -> None:
        try:
            body = self._read_json()
            self.server.signal("api-git-project-action-run", action_key=body.get("action_key", ""))
            self._send_git_operation_result(self.server.git_tools.run_git_project_panel_action(body))
        except Exception as exc:
            self.server.signal("api-git-project-action-run-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_gitignore_save(self) -> None:
        try:
            body = self._read_json()
            project_id = str(body.get("project_id", "") or "").strip()
            project_path = str(body.get("project_path", body.get("repo_dir", "")) or "").strip()
            gitignore_path = str(body.get("path", ".gitignore") or ".gitignore")
            newline = str(body.get("newline", "existing") or "existing")
            lines = body.get("lines", [])
            self.server.signal("api-git-project-gitignore-save", project_id=project_id, path=gitignore_path)
            result = self.server.git_tools.save_project_gitignore(
                project_id=project_id,
                path=project_path,
                gitignore_path=gitignore_path,
                lines=lines,
                newline=newline,
            )
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
        except Exception as exc:
            self.server.signal("api-git-project-gitignore-save-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_commit_start(self) -> None:
        try:
            body = self._read_json()
            self.server.signal("api-git-project-commit-start")
            result = self.server.git_tools.start_git_project_commit_job(body)
            status = HTTPStatus.CONFLICT if result.get("busy") else (HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
            self._send_json(result, status=status)
        except Exception as exc:
            self.server.signal("api-git-project-commit-start-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_commit_cancel(self) -> None:
        try:
            body = self._read_json()
            job_id = str(body.get("job_id", "") or "").strip()
            if not job_id:
                raise ValueError("Commit job id is required.")
            self.server.signal("api-git-project-commit-cancel", job_id=job_id)
            self._send_json(self.server.git_tools.cancel_git_project_commit_job(job_id))
        except Exception as exc:
            self.server.signal("api-git-project-commit-cancel-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_project_commit_stream(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        job_id = str((query.get("job_id") or [""])[0] or "").strip()
        after_raw = str((query.get("after") or [self.headers.get("Last-Event-ID", "0")])[0] or "0")
        try:
            last_seq = int(after_raw)
        except (TypeError, ValueError):
            last_seq = 0
        if not job_id:
            self._send_json({"error": "Commit job id is required."}, status=HTTPStatus.BAD_REQUEST)
            return
        self.server.signal("api-git-project-commit-stream", job_id=job_id)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        deadline = time.time() + 3600
        while time.time() < deadline:
            snapshot = self.server.git_tools.git_project_commit_job_events(job_id, after_seq=last_seq)
            if not snapshot.get("ok"):
                event = {"type": "error", "message": snapshot.get("error", "Unknown commit job."), "job_id": job_id}
                payload = json.dumps(event, ensure_ascii=False, sort_keys=True)
                try:
                    self.wfile.write(f"event: commit\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                return
            events = snapshot.get("events") or []
            for event in events:
                try:
                    last_seq = int(event.get("seq") or last_seq)
                except (TypeError, ValueError):
                    pass
                payload = json.dumps(event, ensure_ascii=False, sort_keys=True)
                try:
                    self.wfile.write(f"id: {last_seq}\nevent: commit\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
            if snapshot.get("done") and not events:
                return
            time.sleep(0.2)
        timeout_event = {"type": "stream_timeout", "job_id": job_id, "message": "Commit job stream timed out."}
        try:
            self.wfile.write(f"event: commit\ndata: {json.dumps(timeout_event, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _handle_git_project_secrets_filter_stream(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        job_id = str((query.get("job_id") or [""])[0] or "").strip()
        after_raw = str((query.get("after") or [self.headers.get("Last-Event-ID", "0")])[0] or "0")
        try:
            last_seq = int(after_raw)
        except (TypeError, ValueError):
            last_seq = 0
        if not job_id:
            self._send_json({"error": "Secrets / Filter scan job id is required."}, status=HTTPStatus.BAD_REQUEST)
            return
        self.server.signal("api-git-project-secrets-filter-stream", job_id=job_id)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        deadline = time.time() + 300
        while time.time() < deadline:
            snapshot = self.server.git_tools.git_project_secrets_filter_job_events(job_id, after_seq=last_seq)
            if not snapshot.get("ok"):
                event = {"type": "error", "message": snapshot.get("error", "Unknown scan job."), "job_id": job_id}
                payload = json.dumps(event, ensure_ascii=False, sort_keys=True)
                try:
                    self.wfile.write(f"event: scan\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                return
            events = snapshot.get("events") or []
            for event in events:
                try:
                    last_seq = int(event.get("seq") or last_seq)
                except (TypeError, ValueError):
                    pass
                payload = json.dumps(event, ensure_ascii=False, sort_keys=True)
                try:
                    self.wfile.write(f"id: {last_seq}\nevent: scan\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
            if snapshot.get("done") and not events:
                return
            time.sleep(0.2)
        timeout_event = {"type": "stream_timeout", "job_id": job_id, "message": "Secrets / Filter scan stream timed out."}
        try:
            self.wfile.write(f"event: scan\ndata: {json.dumps(timeout_event, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _handle_git_patches(self) -> None:
        try:
            self.server.signal("api-git-patches")
            self._send_json(self.server.git_tools.list_patches())
        except Exception as exc:
            self.server.signal("api-git-patches-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_patch_read(self) -> None:
        try:
            body = self._read_json()
            patch_name = str(body.get("patch_name", "") or "").strip()
            if not patch_name:
                raise ValueError("Patch name is required.")
            self.server.signal("api-git-patch-read", patch_name=patch_name)
            self._send_json(self.server.git_tools.read_patch(patch_name))
        except Exception as exc:
            self.server.signal("api-git-patch-read-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_patch_apply(self) -> None:
        try:
            body = self._read_json()
            patch_name = str(body.get("patch_name", "") or "").strip()
            if not patch_name:
                raise ValueError("Patch name is required.")
            target_root = str(body.get("target_root", ".") or ".")
            dry_run = self._coerce_bool(body.get("dry_run", True), default=True)
            reverse = self._coerce_bool(body.get("reverse", False), default=False)
            strict_root = self._coerce_bool(body.get("strict_root", False), default=False)
            self.server.signal(
                "api-git-patch-apply",
                patch_name=patch_name,
                target_root=target_root,
                dry_run=dry_run,
                reverse=reverse,
                strict_root=strict_root,
            )
            result = self.server.git_tools.apply_patch(
                patch_name=patch_name,
                target_root=target_root,
                dry_run=dry_run,
                reverse=reverse,
                strict_root=strict_root,
            )
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
        except Exception as exc:
            self.server.signal("api-git-patch-apply-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_dry_run_read(self) -> None:
        try:
            body = self._read_json()
            run_name = str(body.get("run_name", "") or "").strip()
            if not run_name:
                raise ValueError("Dry-run preview name is required.")
            self.server.signal("api-git-dry-run-read", run_name=run_name)
            self._send_json(self.server.git_tools.read_dry_run(run_name))
        except Exception as exc:
            self.server.signal("api-git-dry-run-read-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_shims(self) -> None:
        try:
            self.server.signal("api-git-shims")
            self._send_json(self.server.git_tools.list_git_shims())
        except Exception as exc:
            self.server.signal("api-git-shims-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_shim_read(self) -> None:
        try:
            body = self._read_json()
            shim_id = str(body.get("shim_id", "") or "").strip()
            if not shim_id:
                raise ValueError("Shim id is required.")
            self.server.signal("api-git-shim-read", shim_id=shim_id)
            self._send_json(self.server.git_tools.read_git_shim(shim_id))
        except Exception as exc:
            self.server.signal("api-git-shim-read-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_shim_run(self) -> None:
        try:
            body = self._read_json()
            shim_id = str(body.get("shim_id", "") or "").strip()
            if not shim_id:
                raise ValueError("Shim id is required.")
            self.server.signal("api-git-shim-run", shim_id=shim_id)
            result = self.server.git_tools.run_git_shim(shim_id)
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
        except Exception as exc:
            self.server.signal("api-git-shim-run-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_shim_delete(self) -> None:
        try:
            body = self._read_json()
            shim_id = str(body.get("shim_id", "") or "").strip()
            if not shim_id:
                raise ValueError("Shim id is required.")
            self.server.signal("api-git-shim-delete", shim_id=shim_id)
            self._send_json(self.server.git_tools.delete_git_shim(shim_id))
        except Exception as exc:
            self.server.signal("api-git-shim-delete-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_shim_ordination(self) -> None:
        try:
            body = self._read_json()
            shim_id = str(body.get("shim_id", "") or "").strip()
            ordained = self._coerce_bool(body.get("ordained", True), default=True)
            if not shim_id:
                raise ValueError("Shim id is required.")
            self.server.signal("api-git-shim-ordination", shim_id=shim_id, ordained=ordained)
            if ordained:
                self._send_json(self.server.git_tools.ordain_git_shim(shim_id))
            else:
                self._send_json(self.server.git_tools.unordain_git_shim(shim_id))
        except Exception as exc:
            self.server.signal("api-git-shim-ordination-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_console_extract(self) -> None:
        try:
            body = self._read_json()
            ai_output = str(body.get("ai_output", "") or "")
            self.server.signal("api-git-console-extract", chars=len(ai_output))
            self._send_json(self.server.git_tools.extract_git_console_shims(ai_output))
        except Exception as exc:
            self.server.signal("api-git-console-extract-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_console_run(self) -> None:
        try:
            body = self._read_json()
            command = str(body.get("command", "") or "")
            repo_dir = str(body.get("repo_dir", ".") or ".")
            self.server.signal("api-git-console-run", chars=len(command), repo_dir=repo_dir)
            result = self.server.git_tools.run_git_operation(
                "git-console",
                command.splitlines()[0][:96] if command.strip() else "Git console command",
                lambda: self.server.git_tools.run_git_console_command(command, repo_dir=repo_dir),
                payload={"command": command, "repo_dir": repo_dir},
            )
            if not result.get("ok") and not result.get("error"):
                nested = result.get("result") if isinstance(result.get("result"), dict) else {}
                result["error"] = (
                    str(result.get("stderr") or "").strip()
                    or str(result.get("stdout") or "").strip()
                    or str(nested.get("stderr") or "").strip()
                    or str(nested.get("stdout") or "").strip()
                    or "Git console command failed."
                )
            self._send_git_operation_result(result)
        except Exception as exc:
            self.server.signal("api-git-console-run-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_control_plan(self) -> None:
        try:
            body = self._read_json()
            prompt = str(body.get("prompt", "") or "")
            self.server.signal("api-git-control-plan", chars=len(prompt))
            self._send_json(self.server.git_tools.plan_git_control(prompt))
        except Exception as exc:
            self.server.signal("api-git-control-plan-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_ai_shim(self) -> None:
        try:
            body = self._read_json()
            prompt = str(body.get("prompt", "") or "").strip() or "Recommend the next git-control shim."
            self.server.signal("api-git-ai-shim", chars=len(prompt))
            result = self.server.git_tools.ask_git_ai(prompt, chat_callable=self.server.computer.chat)
            self._send_json(result)
        except Exception as exc:
            self.server.signal("api-git-ai-shim-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_server_status(self) -> None:
        try:
            self.server.signal("api-git-server-status")
            self._send_json(self.server.git_tools.git_server_status())
        except Exception as exc:
            self.server.signal("api-git-server-status-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_server_action(self) -> None:
        try:
            body = self._read_json()
            action = str(body.get("action", "") or "").strip()
            if not action:
                raise ValueError("Git server action is required.")
            self.server.signal("api-git-server-action", action=action)
            result = self.server.git_tools.run_git_operation(
                "git-server-action",
                f"Git server {action}",
                lambda: self.server.git_tools.git_server_action(action),
                payload={"action": action},
            )
            self._send_git_operation_result(result)
        except Exception as exc:
            self.server.signal("api-git-server-action-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_server_target_prefunk(self) -> None:
        try:
            body = self._read_json()
            repo_dir = str(body.get("repo_dir", ".") or ".")
            self.server.signal("api-git-server-target-prefunk", repo_dir=repo_dir)
            self._send_json(self.server.git_tools.git_server_target_prefunk(repo_dir))
        except Exception as exc:
            self.server.signal("api-git-server-target-prefunk-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_server_remote_configure(self) -> None:
        try:
            body = self._read_json()
            remote = str(body.get("remote", "origin") or "origin")
            owner = str(body.get("owner", "local") or "local")
            repo_name = str(body.get("repo", "") or "")
            protocol = str(body.get("protocol", "http") or "http")
            repo_dir = str(body.get("repo_dir", ".") or ".")
            self.server.signal(
                "api-git-server-remote-configure",
                remote=remote,
                owner=owner,
                repo=repo_name,
                protocol=protocol,
                repo_dir=repo_dir,
            )
            result = self.server.git_tools.configure_git_server_remote(
                repo_dir=repo_dir,
                remote=remote,
                owner=owner,
                repo_name=repo_name,
                protocol=protocol,
            )
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
        except Exception as exc:
            self.server.signal("api-git-server-remote-configure-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_server_setup_local(self) -> None:
        try:
            body = self._read_json()
            remote = str(body.get("remote", "local-gitea") or "local-gitea")
            owner = str(body.get("owner", "local") or "local")
            repo_name = str(body.get("repo", "") or "")
            protocol = str(body.get("protocol", "http") or "http")
            repo_dir = str(body.get("repo_dir", ".") or ".")
            switch_origin = bool(body.get("switch_origin", False))
            self.server.signal(
                "api-git-server-setup-local",
                remote=remote,
                owner=owner,
                repo=repo_name,
                protocol=protocol,
                repo_dir=repo_dir,
                switch_origin=switch_origin,
            )
            result = self.server.git_tools.run_git_operation(
                "git-server-setup-local",
                "Set up local Git server",
                lambda: self.server.git_tools.setup_local_git_server(
                    repo_dir=repo_dir,
                    remote=remote,
                    owner=owner,
                    repo_name=repo_name,
                    protocol=protocol,
                    switch_origin=switch_origin,
                ),
                payload={"repo_dir": repo_dir, "remote": remote, "owner": owner, "repo": repo_name, "protocol": protocol, "switch_origin": switch_origin},
            )
            self._send_git_operation_result(result)
        except Exception as exc:
            self.server.signal("api-git-server-setup-local-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_server_push_local(self) -> None:
        try:
            body = self._read_json()
            remote = str(body.get("remote", "local-gitea") or "local-gitea")
            owner = str(body.get("owner", "local") or "local")
            repo_name = str(body.get("repo", "") or "")
            protocol = str(body.get("protocol", "http") or "http")
            repo_dir = str(body.get("repo_dir", ".") or ".")
            switch_origin = bool(body.get("switch_origin", False))
            self.server.signal(
                "api-git-server-push-local",
                remote=remote,
                owner=owner,
                repo=repo_name,
                protocol=protocol,
                repo_dir=repo_dir,
                switch_origin=switch_origin,
            )
            result = self.server.git_tools.run_git_operation(
                "git-server-push-local",
                "Push to local Git server",
                lambda: self.server.git_tools.push_local_git_server(
                    repo_dir=repo_dir,
                    remote=remote,
                    owner=owner,
                    repo_name=repo_name,
                    protocol=protocol,
                    switch_origin=switch_origin,
                ),
                payload={"repo_dir": repo_dir, "remote": remote, "owner": owner, "repo": repo_name, "protocol": protocol, "switch_origin": switch_origin},
            )
            self._send_git_operation_result(result)
        except Exception as exc:
            self.server.signal("api-git-server-push-local-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_server_external_remote(self) -> None:
        try:
            body = self._read_json()
            remote = str(body.get("remote", "origin") or "origin")
            url = str(body.get("url", "") or "")
            repo_dir = str(body.get("repo_dir", ".") or ".")
            add_if_missing = bool(body.get("add_if_missing", True))
            self.server.signal("api-git-server-external-remote", remote=remote, repo_dir=repo_dir)
            result = self.server.git_tools.configure_external_git_remote(
                repo_dir=repo_dir,
                remote=remote,
                url=url,
                add_if_missing=add_if_missing,
            )
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
        except Exception as exc:
            self.server.signal("api-git-server-external-remote-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_server_mirror_plan(self) -> None:
        try:
            body = self._read_json()
            owner = str(body.get("owner", "local") or "local")
            repo_name = str(body.get("repo", "") or "")
            external_url = str(body.get("external_url", "") or "")
            external_username = str(body.get("external_username", "") or "")
            self.server.signal("api-git-server-mirror-plan", owner=owner, repo=repo_name)
            result = self.server.git_tools.plan_gitea_push_mirror(
                owner=owner,
                repo_name=repo_name,
                external_url=external_url,
                external_username=external_username,
            )
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
        except Exception as exc:
            self.server.signal("api-git-server-mirror-plan-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_git_server_mirror_setup(self) -> None:
        try:
            body = self._read_json()
            owner = str(body.get("owner", "local") or "local")
            repo_name = str(body.get("repo", "") or "")
            external_url = str(body.get("external_url", "") or "")
            external_username = str(body.get("external_username", "") or "")
            external_password = str(body.get("external_password", "") or "")
            interval = str(body.get("interval", "8h") or "8h")
            sync_on_commit = bool(body.get("sync_on_commit", True))
            self.server.signal("api-git-server-mirror-setup", owner=owner, repo=repo_name)
            result = self.server.git_tools.run_git_operation(
                "git-server-mirror-setup",
                "Set up server push mirror",
                lambda: self.server.git_tools.setup_gitea_push_mirror(
                    owner=owner,
                    repo_name=repo_name,
                    external_url=external_url,
                    external_username=external_username,
                    external_password=external_password,
                    interval=interval,
                    sync_on_commit=sync_on_commit,
                ),
                payload={"owner": owner, "repo": repo_name, "external_url": external_url, "external_username": external_username, "interval": interval, "sync_on_commit": sync_on_commit},
            )
            self._send_git_operation_result(result)
        except Exception as exc:
            self.server.signal("api-git-server-mirror-setup-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

