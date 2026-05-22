from __future__ import annotations

import json
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from main_computer.config import MainComputerConfig
from main_computer.router import MainComputer


DEFAULT_OPENCLAW_BRIDGE_PORT = 8767
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_loopback_host(host: str) -> bool:
    normalized = (host or "").strip().lower()
    return normalized in _LOOPBACK_HOSTS


class OpenClawBridgeServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        config: MainComputerConfig,
        *,
        token: str | None = None,
        verbose: bool = True,
    ) -> None:
        super().__init__(server_address, OpenClawBridgeHandler)
        self.config = config
        self.computer = MainComputer.build(config)
        self.token = token.strip() if token and token.strip() else None
        self.verbose = verbose

    def signal(self, name: str, **fields: object) -> None:
        if not self.verbose:
            return
        parts = [f"{key}={value}" for key, value in fields.items()]
        line = f"[signal] {name}"
        if parts:
            line += " " + " ".join(parts)
        print(line)


class OpenClawBridgeHandler(BaseHTTPRequestHandler):
    server: OpenClawBridgeServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        self.server.signal("http-access", address=self.address_string(), message=format % args)

    def do_GET(self) -> None:
        self.server.signal("http-request", method="GET", path=self.path)
        if not self._authorize():
            return
        if self.path == "/v1/health":
            self._send_json(self._health_payload())
            return
        if self.path == "/v1/capabilities":
            self._send_json(self._capabilities_payload())
            return
        if self.path == "/v1/projects":
            self._send_json(self._projects_payload())
            return
        self._send_json({"error": f"Unknown path: {self.path}"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        self.server.signal("http-request", method="POST", path=self.path)
        if not self._authorize():
            return
        if self.path == "/v1/chat":
            self._handle_chat()
            return
        if self.path == "/v1/project/inspect":
            self._handle_project_inspect()
            return
        self._send_json({"error": f"Unknown path: {self.path}"}, status=HTTPStatus.NOT_FOUND)

    def _authorize(self) -> bool:
        token = self.server.token
        if token is None:
            return True
        header = self.headers.get("Authorization", "").strip()
        if header == f"Bearer {token}":
            return True
        self.server.signal("bridge-auth-rejected", address=self.client_address[0], path=self.path)
        self._send_json({"error": "Unauthorized."}, status=HTTPStatus.UNAUTHORIZED)
        return False

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object.")
        return payload

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _health_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "service": "main_computer_openclaw_bridge",
            "patch_level": self.server.config.patch_level,
            "provider": self.server.config.provider,
            "model": self.server.config.model,
            "workspace": str(self.server.config.workspace),
            "auth_required": self.server.token is not None,
        }

    def _capabilities_payload(self) -> dict[str, Any]:
        return {
            **self._health_payload(),
            "routes": {
                "GET": ["/v1/health", "/v1/capabilities", "/v1/projects"],
                "POST": ["/v1/chat", "/v1/project/inspect"],
            },
            "tools": [
                {
                    "name": "main_computer_chat",
                    "description": "Ask the local Main Computer a question with workspace context.",
                },
                {
                    "name": "main_computer_list_projects",
                    "description": "List known workspace projects from the local Main Computer.",
                },
                {
                    "name": "main_computer_inspect_project",
                    "description": "Inspect one named workspace project.",
                },
            ],
        }

    def _projects_payload(self) -> dict[str, Any]:
        projects = [
            {
                "name": project.name,
                "path": str(project.path),
                "markers": list(project.markers),
                "child_count": project.child_count,
                "file_count": project.file_count,
            }
            for project in self.server.computer.catalog.list_projects()
        ]
        return {
            "workspace": str(self.server.config.workspace),
            "count": len(projects),
            "projects": projects,
        }

    def _handle_chat(self) -> None:
        try:
            body = self._read_json()
            prompt = str(body.get("prompt", "")).strip()
            if not prompt:
                self._send_json({"error": "Prompt is required."}, status=HTTPStatus.BAD_REQUEST)
                return
            response = self.server.computer.chat(prompt)
            payload = asdict(response)
            payload["prompt"] = prompt
            self._send_json(payload)
        except Exception as exc:
            self.server.signal("bridge-chat-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)

    def _handle_project_inspect(self) -> None:
        try:
            body = self._read_json()
            name = str(body.get("name", "")).strip()
            if not name:
                self._send_json({"error": "Project name is required."}, status=HTTPStatus.BAD_REQUEST)
                return
            project = self.server.computer.catalog.inspect(name)
            self._send_json(
                {
                    "name": project.name,
                    "path": str(project.path),
                    "markers": list(project.markers),
                    "child_count": project.child_count,
                    "file_count": project.file_count,
                }
            )
        except KeyError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.server.signal("bridge-project-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)


def serve(
    config: MainComputerConfig,
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_OPENCLAW_BRIDGE_PORT,
    token: str | None = None,
    verbose: bool = True,
) -> None:
    if not _is_loopback_host(host) and not (token and token.strip()):
        raise ValueError("Non-loopback OpenClaw bridge binds require --token.")
    server = OpenClawBridgeServer((host, port), config, token=token, verbose=verbose)
    server.signal(
        "openclaw-bridge-start",
        host=host,
        port=server.server_port,
        provider=config.provider,
        model=config.model,
        auth_required=server.token is not None,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.signal("openclaw-bridge-stop", reason="keyboard-interrupt")
    finally:
        server.server_close()
