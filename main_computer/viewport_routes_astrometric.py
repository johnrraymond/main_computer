from __future__ import annotations

import json
from http import HTTPStatus
from urllib.error import URLError
from urllib.parse import urlsplit

from main_computer.astrometric_renderer_service import AstrometricRendererError


class ViewportAstrometricRoutesMixin:
    def _handle_astrometric_status(self) -> None:
        self.server.signal("api-astrometric-status")
        self._send_json(self.server.astrometric_renderer.status())

    def _handle_astrometric_action(self) -> None:
        try:
            body = self._read_json()
            action = body.get("action") if isinstance(body, dict) else ""
            result = self.server.astrometric_renderer.action(str(action or "status"))
            self.server.signal("api-astrometric-action", action=action, ok=result.get("ok"))
            self._send_json(result, HTTPStatus.OK if result.get("ok", True) else HTTPStatus.BAD_GATEWAY)
        except AstrometricRendererError as exc:
            self.server.signal("api-astrometric-action-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_GATEWAY)
        except Exception as exc:
            self.server.signal("api-astrometric-action-unhandled-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_astrometric_camera(self) -> None:
        try:
            payload = self._read_json()
            if not isinstance(payload, dict):
                payload = {}
            result = self.server.astrometric_renderer.send_camera(payload)
            self.server.signal("api-astrometric-camera", control=payload.get("type") or payload.get("action"))
            self._send_json(result, HTTPStatus.OK if int(result.get("status", 200)) < 400 else HTTPStatus.BAD_GATEWAY)
        except AstrometricRendererError as exc:
            self.server.signal("api-astrometric-camera-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_GATEWAY)
        except Exception as exc:
            self.server.signal("api-astrometric-camera-unhandled-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_astrometric_frame(self) -> None:
        try:
            response = self.server.astrometric_renderer.fetch_frame()
            if response.status >= 400:
                self._send_json(
                    {
                        "ok": False,
                        "error": response.body.decode("utf-8", errors="replace"),
                        "status": response.status,
                    },
                    HTTPStatus.BAD_GATEWAY,
                )
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", response.content_type or "image/jpeg")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)
        except AstrometricRendererError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_GATEWAY)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            self.server.signal("api-astrometric-frame-client-disconnected", error=exc)

    def _handle_astrometric_stream(self) -> None:
        self.server.signal("api-astrometric-stream-open")
        try:
            with self.server.astrometric_renderer.open_stream() as upstream:
                content_type = upstream.headers.get(
                    "Content-Type",
                    "multipart/x-mixed-replace; boundary=frame",
                )
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = upstream.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            self.server.signal("api-astrometric-stream-client-disconnected", error=exc)
        except (AstrometricRendererError, URLError, TimeoutError, OSError) as exc:
            if not self.wfile.closed:
                try:
                    self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_GATEWAY)
                except Exception:
                    pass
            self.server.signal("api-astrometric-stream-error", error=exc)
