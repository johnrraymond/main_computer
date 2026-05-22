#!/usr/bin/env python3
r"""
openclaw_ops_smoke.py

Tiny local operations bridge for OpenClaw/Main Computer experiments.

Purpose:
- Give OpenClaw safe read-only visibility into selected machine paths.
- Expose a few explicit operator actions as stubs.
- Avoid arbitrary shell execution.
- Require a bearer token.
- Bind to localhost by default.

Run:

  set MAIN_COMPUTER_OPENCLAW_TOKEN=make-a-new-token
  for /f "delims=" %I in ('python -c "from pathlib import Path; import main_computer.openclaw_ops_smoke as m; print(Path(m.__file__).resolve().parents[1])"') do set OPENCLAW_OPS_ROOT=%I
  python openclaw_ops_smoke.py serve --host 127.0.0.1 --port 8791

Smoke test:

  python openclaw_ops_smoke.py smoke --base-url http://127.0.0.1:8791 --token make-a-new-token

Endpoints:

  GET  /v1/health
  GET  /v1/capabilities
  GET  /v1/fs/list?path=.
  GET  /v1/fs/read?path=some/file.txt
  POST /v1/action/restart-website

The restart website endpoint is a stub unless you replace restart_website_stub().
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime as _dt
import json
import os
import platform
import sys
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8791
DEFAULT_MAX_READ_BYTES = 256_000


@dataclasses.dataclass(frozen=True)
class Config:
    token: str
    root: Path
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES


def utc_now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat()


def json_bytes(obj: Any, status: int = 200) -> tuple[int, bytes]:
    return status, json.dumps(obj, indent=2, sort_keys=True).encode("utf-8")


def error_json(message: str, status: int = 400, **extra: Any) -> tuple[int, bytes]:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return json_bytes(payload, status=status)


def resolve_under_root(root: Path, user_path: str) -> Path:
    """
    Resolve user_path under root and reject traversal outside root.
    """
    root_resolved = root.resolve()
    candidate = (root_resolved / user_path).resolve()

    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        raise PermissionError(f"path escapes allowed root: {user_path!r}")

    return candidate


def is_probably_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def list_path(config: Config, user_path: str) -> dict[str, Any]:
    target = resolve_under_root(config.root, user_path)

    if not target.exists():
        raise FileNotFoundError(str(target))

    if target.is_file():
        stat = target.stat()
        return {
            "ok": True,
            "kind": "file",
            "path": str(target),
            "relative_path": str(target.relative_to(config.root.resolve())),
            "size": stat.st_size,
            "modified": _dt.datetime.fromtimestamp(stat.st_mtime, _dt.UTC).isoformat(),
        }

    if not target.is_dir():
        return {
            "ok": True,
            "kind": "other",
            "path": str(target),
        }

    entries: list[dict[str, Any]] = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            stat = child.stat()
            entries.append(
                {
                    "name": child.name,
                    "kind": "dir" if child.is_dir() else "file" if child.is_file() else "other",
                    "size": stat.st_size if child.is_file() else None,
                    "modified": _dt.datetime.fromtimestamp(stat.st_mtime, _dt.UTC).isoformat(),
                }
            )
        except OSError as exc:
            entries.append(
                {
                    "name": child.name,
                    "kind": "error",
                    "error": str(exc),
                }
            )

    return {
        "ok": True,
        "kind": "dir",
        "path": str(target),
        "relative_path": "." if target == config.root.resolve() else str(target.relative_to(config.root.resolve())),
        "count": len(entries),
        "entries": entries,
    }


def read_path(config: Config, user_path: str) -> dict[str, Any]:
    target = resolve_under_root(config.root, user_path)

    if not target.exists():
        raise FileNotFoundError(str(target))
    if not target.is_file():
        raise IsADirectoryError(str(target))

    stat = target.stat()
    if stat.st_size > config.max_read_bytes:
        raise ValueError(
            f"file too large for smoke read: {stat.st_size} bytes > {config.max_read_bytes} bytes"
        )

    data = target.read_bytes()
    relative = str(target.relative_to(config.root.resolve()))

    if is_probably_text(data):
        return {
            "ok": True,
            "kind": "text",
            "path": str(target),
            "relative_path": relative,
            "size": len(data),
            "content": data.decode("utf-8"),
        }

    return {
        "ok": True,
        "kind": "binary",
        "path": str(target),
        "relative_path": relative,
        "size": len(data),
        "encoding": "base64",
        "content_base64": base64.b64encode(data).decode("ascii"),
    }


def restart_website_stub(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Stub only.

    Replace this with a real implementation later, for example:
    - call a trusted deployment webhook
    - restart a known Windows service
    - touch a known app-specific restart file
    - call a cloud provider API

    Do not turn this into arbitrary command execution.
    """
    site = str(payload.get("site", "default")).strip() or "default"
    reason = str(payload.get("reason", "manual operator request")).strip()

    allowed_sites = {
        "default",
        "staging",
        "production",
    }

    if site not in allowed_sites:
        raise PermissionError(f"site is not allowlisted: {site!r}")

    return {
        "ok": True,
        "action": "restart_website",
        "mode": "stub",
        "site": site,
        "reason": reason,
        "message": "Stub only: no remote website was restarted.",
        "timestamp": utc_now(),
    }


class OpsHandler(BaseHTTPRequestHandler):
    server_version = "OpenClawOpsSmoke/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (utc_now(), fmt % args))

    @property
    def config(self) -> Config:
        return self.server.config  # type: ignore[attr-defined]

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self) -> bool:
        expected = f"Bearer {self.config.token}"
        return self.headers.get("Authorization", "") == expected

    def _require_auth(self) -> bool:
        if self._auth_ok():
            return True
        status, body = error_json("missing or invalid bearer token", status=401)
        self._send(status, body)
        return False

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"request body must be valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def do_GET(self) -> None:
        if not self._require_auth():
            return

        try:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)

            if parsed.path == "/v1/health":
                status, body = json_bytes(
                    {
                        "ok": True,
                        "service": "openclaw_ops_smoke",
                        "time": utc_now(),
                        "root": str(self.config.root.resolve()),
                        "host": platform.node(),
                        "platform": platform.platform(),
                        "python": sys.version.split()[0],
                    }
                )

            elif parsed.path == "/v1/capabilities":
                status, body = json_bytes(
                    {
                        "ok": True,
                        "read_only_root": str(self.config.root.resolve()),
                        "max_read_bytes": self.config.max_read_bytes,
                        "routes": {
                            "GET": [
                                "/v1/health",
                                "/v1/capabilities",
                                "/v1/fs/list?path=.",
                                "/v1/fs/read?path=relative/file.txt",
                            ],
                            "POST": [
                                "/v1/action/restart-website",
                            ],
                        },
                        "actions": [
                            {
                                "name": "restart_website",
                                "mode": "stub",
                                "description": "Allowlisted stub action. Does not perform a real restart yet.",
                            }
                        ],
                    }
                )

            elif parsed.path == "/v1/fs/list":
                rel = query.get("path", ["."])[0]
                status, body = json_bytes(list_path(self.config, rel))

            elif parsed.path == "/v1/fs/read":
                rel = query.get("path", [""])[0]
                if not rel:
                    status, body = error_json("missing query parameter: path", status=400)
                else:
                    status, body = json_bytes(read_path(self.config, rel))

            else:
                status, body = error_json(f"unknown route: {parsed.path}", status=404)

        except Exception as exc:
            status, body = error_json(
                str(exc),
                status=500,
                traceback=traceback.format_exc(limit=4),
            )

        self._send(status, body)

    def do_POST(self) -> None:
        if not self._require_auth():
            return

        try:
            parsed = urllib.parse.urlparse(self.path)
            payload = self._read_json_body()

            if parsed.path == "/v1/action/restart-website":
                status, body = json_bytes(restart_website_stub(payload))
            else:
                status, body = error_json(f"unknown route: {parsed.path}", status=404)

        except Exception as exc:
            status, body = error_json(
                str(exc),
                status=500,
                traceback=traceback.format_exc(limit=4),
            )

        self._send(status, body)


class OpsServer(ThreadingHTTPServer):
    config: Config


def serve(args: argparse.Namespace) -> None:
    token = args.token or os.environ.get("MAIN_COMPUTER_OPENCLAW_TOKEN")
    if not token:
        raise SystemExit("Set MAIN_COMPUTER_OPENCLAW_TOKEN or pass --token.")

    root = Path(args.root or os.environ.get("OPENCLAW_OPS_ROOT") or default_ops_root()).resolve()
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Allowed root does not exist or is not a directory: {root}")

    config = Config(
        token=token,
        root=root,
        max_read_bytes=args.max_read_bytes,
    )

    server = OpsServer((args.host, args.port), OpsHandler)
    server.config = config

    print(f"openclaw_ops_smoke listening on http://{args.host}:{args.port}")
    print(f"read-only root: {root}")
    print("auth: bearer token required")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()


def http_json(method: str, url: str, token: str, payload: dict[str, Any] | None = None, timeout: int = 10) -> Any:
    data = None
    headers = {"Authorization": f"Bearer {token}"}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def smoke(args: argparse.Namespace) -> None:
    base = args.base_url.rstrip("/")
    token = args.token or os.environ.get("MAIN_COMPUTER_OPENCLAW_TOKEN")
    if not token:
        raise SystemExit("Set MAIN_COMPUTER_OPENCLAW_TOKEN or pass --token.")

    checks = [
        ("health", "GET", f"{base}/v1/health", None),
        ("capabilities", "GET", f"{base}/v1/capabilities", None),
        ("list-root", "GET", f"{base}/v1/fs/list?path=.", None),
        (
            "restart-website-stub",
            "POST",
            f"{base}/v1/action/restart-website",
            {"site": "staging", "reason": "smoke test"},
        ),
    ]

    failed = 0
    for name, method, url, payload in checks:
        try:
            result = http_json(method, url, token, payload=payload, timeout=args.timeout)
            ok = bool(result.get("ok"))
            print(f"[{'PASS' if ok else 'FAIL'}] {name}")
            if args.verbose:
                print(json.dumps(result, indent=2))
            if not ok:
                failed += 1
        except Exception as exc:
            failed += 1
            print(f"[FAIL] {name}: {exc}")

    if failed:
        raise SystemExit(f"{failed} smoke check(s) failed")

    print("all smoke checks passed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local OpenClaw operations smoke bridge.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="Run the local operations bridge.")
    p_serve.add_argument("--host", default=DEFAULT_HOST)
    p_serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_serve.add_argument("--token", default=None)
    p_serve.add_argument("--root", default=None, help="Read-only root. Defaults to OPENCLAW_OPS_ROOT or the repository root containing this script.")
    p_serve.add_argument("--max-read-bytes", type=int, default=DEFAULT_MAX_READ_BYTES)
    p_serve.set_defaults(func=serve)

    p_smoke = sub.add_parser("smoke", help="Run smoke checks against the bridge.")
    p_smoke.add_argument("--base-url", default=f"http://{DEFAULT_HOST}:{DEFAULT_PORT}")
    p_smoke.add_argument("--token", default=None)
    p_smoke.add_argument("--timeout", type=int, default=10)
    p_smoke.add_argument("--verbose", action="store_true")
    p_smoke.set_defaults(func=smoke)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
