from __future__ import annotations

from main_computer.viewport_state import *  # noqa: F401,F403

class ViewportTaskRoutesMixin:
    def _handle_task_overview(self) -> None:
        try:
            body = self._read_json()
            query = str(body.get("query", "") or "").strip()
            limit = max(5, min(100, int(body.get("limit", 24) or 24)))
            include_all = self._coerce_bool(body.get("include_all", False), default=False)
            include_connections = self._coerce_bool(body.get("include_connections", True), default=True)
            self.server.signal(
                "api-task-overview",
                query=query,
                limit=limit,
                include_all=include_all,
                include_connections=include_connections,
            )
            self._send_json(
                self.server.task_manager.snapshot(
                    query=query,
                    limit=limit,
                    include_all=include_all,
                    include_connections=include_connections,
                )
            )
        except Exception as exc:
            self.server.signal("api-task-overview-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_task_action(self) -> None:
        try:
            body = self._read_json()
            action = str(body.get("action", "") or "").strip()
            if not action:
                raise ValueError("Task action is required.")
            pid = body.get("pid")
            pid_value = int(pid) if pid not in {None, ""} else None
            force = self._coerce_bool(body.get("force", False), default=False)
            confirm = self._coerce_bool(body.get("confirm", False), default=False)
            self.server.signal("api-task-action", action=action, pid=pid_value, force=force, confirm=confirm)
            result = self.server.task_manager.perform_action(action=action, pid=pid_value, force=force, confirm=confirm)
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
        except Exception as exc:
            self.server.signal("api-task-action-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_task_schedules(self) -> None:
        try:
            self.server.signal("api-task-schedules")
            self._send_json(self.server.task_manager.list_schedules())
        except Exception as exc:
            self.server.signal("api-task-schedules-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_task_schedule_create(self) -> None:
        try:
            body = self._read_json()
            action = str(body.get("action", "") or "").strip()
            run_at = str(body.get("run_at", "") or "").strip()
            note = str(body.get("note", "") or "").strip()
            payload = body.get("payload") or {}
            if not isinstance(payload, dict):
                raise ValueError("Schedule payload must be an object.")
            self.server.signal("api-task-schedule-create", action=action, run_at=run_at)
            result = self.server.task_manager.create_schedule(action=action, run_at=run_at, note=note, payload=payload)
            self._send_json(result)
        except Exception as exc:
            self.server.signal("api-task-schedule-create-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_task_schedule_delete(self) -> None:
        try:
            body = self._read_json()
            schedule_id = str(body.get("schedule_id", "") or "").strip()
            if not schedule_id:
                raise ValueError("Schedule id is required.")
            self.server.signal("api-task-schedule-delete", schedule_id=schedule_id)
            self._send_json(self.server.task_manager.delete_schedule(schedule_id=schedule_id))
        except Exception as exc:
            self.server.signal("api-task-schedule-delete-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_task_ai(self) -> None:
        try:
            body = self._read_json()
            instruction = str(body.get("instruction", "") or "").strip() or "Explain what the operator should watch, restart, stop, or schedule next."
            query = str(body.get("query", "") or "").strip()
            limit = max(5, min(100, int(body.get("limit", 24) or 24)))
            include_all = self._coerce_bool(body.get("include_all", False), default=False)
            include_connections = self._coerce_bool(body.get("include_connections", True), default=True)
            self.server.signal("api-task-ai", query=query, limit=limit)
            brief = self.server.task_manager.ai_brief(
                instruction=instruction,
                query=query,
                limit=limit,
                include_all=include_all,
                include_connections=include_connections,
            )
            response = self.server.computer.chat(brief["prompt"])
            self._send_json(
                {
                    "ok": True,
                    "content": response.content,
                    "provider": response.provider,
                    "model": response.model,
                    "snapshot": brief["snapshot"],
                    "prompt": brief["prompt"],
                }
            )
        except Exception as exc:
            self.server.signal("api-task-ai-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
