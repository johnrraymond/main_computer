from __future__ import annotations

import ipaddress
import json
from http import HTTPStatus

from main_computer.viewport_state import *  # noqa: F401,F403


class ViewportConductorRoutesMixin:
    """HTTP control surface for the local conductor subprocess scheduler."""

    def _conductor_client_is_local(self) -> bool:
        host = self.client_address[0] if self.client_address else ""
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return host.lower() in {"localhost"}

    def _handle_conductor_status(self) -> None:
        try:
            if not self._conductor_client_is_local():
                self._send_json({"ok": False, "error": "Conductor status is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            status = self.server.conductor.status()
            self.server.signal("api-conductor-status", jobs=status.get("counts", {}).get("jobs", 0), scheduled=status.get("counts", {}).get("scheduled", 0))
            self._send_json(status)
        except Exception as exc:
            self.server.signal("api-conductor-status-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_conductor_action(self) -> None:
        try:
            if not self._conductor_client_is_local():
                self._send_json({"ok": False, "error": "Conductor actions are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            action = str(body.get("action") or "").strip()
            if not action:
                raise ValueError("action is required.")
            payload = body.get("payload") or {}
            if isinstance(payload, str):
                payload = json.loads(payload or "{}")
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object.")
            run_at = str(body.get("run_at") or "").strip() or None
            note = str(body.get("note") or "").strip()
            confirm = self._coerce_bool(body.get("confirm"), default=False)
            result = self.server.conductor.submit(action=action, payload=payload, run_at=run_at, confirm=confirm, note=note)
            self.server.signal(
                "api-conductor-action",
                action=action,
                scheduled=bool(result.get("scheduled")),
                ok=bool(result.get("ok")),
            )
            self._send_json(result, status=HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-conductor-action-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_conductor_run_due(self) -> None:
        try:
            if not self._conductor_client_is_local():
                self._send_json({"ok": False, "error": "Conductor scheduling is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            now = str(body.get("now") or "").strip() or None
            limit = int(body.get("limit") or 10)
            result = self.server.conductor.run_due(now=now, limit=limit)
            self.server.signal("api-conductor-run-due", ran=int(result.get("ran") or 0), ok=bool(result.get("ok")))
            self._send_json(result, status=HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-conductor-run-due-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
