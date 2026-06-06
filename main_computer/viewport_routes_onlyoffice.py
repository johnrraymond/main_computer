from __future__ import annotations

import base64
import binascii
import io
import hashlib
import hmac
import mimetypes
import posixpath
import tempfile
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from main_computer.viewport_state import *  # noqa: F401,F403


class ViewportOnlyOfficeRoutesMixin:
    """Storage-service routes for the standalone ONLYOFFICE application.

    The existing Spreadsheet app keeps its JSON/RevoGrid workflow.  These routes
    are deliberately .xlsx-native because ONLYOFFICE owns workbook visuals and
    saves edited XLSX bytes back through the callback URL.
    """

    def _handle_onlyoffice_files(self) -> None:
        try:
            self._read_json()
            files = [
                self._onlyoffice_file_payload(path)
                for path in sorted(self._onlyoffice_root().rglob("*"))
                if path.is_file() and path.suffix.lower() == ".xlsx"
            ]
            self.server.signal("api-onlyoffice-files", count=len(files))
            self._send_json({"ok": True, "root": "onlyoffice", "files": files, "count": len(files)})
        except Exception as exc:
            self.server.signal("api-onlyoffice-error", route="files", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_onlyoffice_status(self) -> None:
        try:
            self._read_json()
            status = self._onlyoffice_status_payload()
            self.server.signal(
                "api-onlyoffice-status",
                public_url=status["public_url"],
                server_online=status["server_probe"]["ok"],
            )
            self._send_json({"ok": True, **status})
        except Exception as exc:
            self.server.signal("api-onlyoffice-error", route="status", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_onlyoffice_upload(self) -> None:
        try:
            body = self._read_json()
            raw_name = str(body.get("path") or body.get("name") or "").strip()
            path = self._onlyoffice_safe_path(raw_name, must_exist=False)
            expected = str(body.get("expected_content_hash", "") or "")
            if path.exists() and expected and expected != self._onlyoffice_content_hash(path):
                self._send_json({"ok": False, "conflict": True, "error": "expected_content_hash is stale."}, status=HTTPStatus.CONFLICT)
                return
            encoded = str(body.get("content_base64") or "")
            if not encoded:
                raise ValueError("content_base64 is required.")
            try:
                data = base64.b64decode(encoded, validate=True)
            except binascii.Error as exc:
                raise ValueError("content_base64 is not valid base64.") from exc
            if not self._onlyoffice_looks_like_xlsx(data):
                raise ValueError("ONLYOFFICE uploads must be valid .xlsx zip packages.")
            self._onlyoffice_atomic_write(path, data)
            self.server.signal("api-onlyoffice-upload", path=path.name, bytes=len(data))
            self._send_json({"ok": True, **self._onlyoffice_file_payload(path)})
        except Exception as exc:
            self.server.signal("api-onlyoffice-error", route="upload", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_onlyoffice_create(self) -> None:
        try:
            body = self._read_json()
            raw_path = str(body.get("path") or "Book.xlsx")
            path = self._onlyoffice_safe_path(raw_path, must_exist=False)
            if path.exists() and not self._coerce_bool(body.get("overwrite"), default=False):
                raise ValueError("ONLYOFFICE workbook already exists.")
            data = self._onlyoffice_blank_xlsx()
            self._onlyoffice_atomic_write(path, data)
            self.server.signal("api-onlyoffice-create", path=path.name, bytes=len(data))
            self._send_json({"ok": True, **self._onlyoffice_file_payload(path)})
        except Exception as exc:
            self.server.signal("api-onlyoffice-error", route="create", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_onlyoffice_status(self) -> None:
        try:
            body = self._read_json()
            probe = self._coerce_bool(body.get("probe"), default=False)
            try:
                timeout_s = max(0.25, min(float(body.get("timeout_s", 2.0) or 2.0), 10.0))
            except (TypeError, ValueError):
                timeout_s = 2.0

            configured_public_url = self._onlyoffice_public_url()
            browser_public_url_override = self._onlyoffice_browser_public_url_override()
            public_url = self._onlyoffice_browser_document_server_url()
            internal_url = self._onlyoffice_internal_url()
            callback_base_url = self._onlyoffice_callback_base_url()
            browser_public_url_candidates = self._onlyoffice_browser_public_url_candidates()
            api_url = f"{public_url}/web-apps/apps/api/documents/api.js"
            api_reachable = None
            api_error = ""
            if probe:
                try:
                    request = Request(api_url, headers={"User-Agent": "main-computer-onlyoffice-status"})
                    with urlopen(request, timeout=timeout_s) as response:
                        response.read(512)
                        api_reachable = 200 <= int(response.status) < 400
                except Exception as exc:
                    api_reachable = False
                    api_error = str(exc)
            files_count = sum(1 for path in self._onlyoffice_root().rglob("*.xlsx") if path.is_file())
            self.server.signal("api-onlyoffice-status", reachable=api_reachable, public_url=public_url)
            self._send_json({
                "ok": True,
                "enabled": bool(getattr(self.server.config, "onlyoffice_enabled", False)),
                "mode": self._onlyoffice_effective_mode(),
                "default_mode": self._onlyoffice_effective_mode(),
                "configured_public_url": configured_public_url,
                "browser_public_url_override": browser_public_url_override,
                "public_url": public_url,
                "internal_url": internal_url,
                "callback_base_url": callback_base_url,
                "document_server_url": public_url,
                "browser_public_url_candidates": browser_public_url_candidates,
                "api_url": api_url,
                "public_api_url": api_url,
                "api_reachable": api_reachable,
                "api_error": api_error,
                "server_probe": {
                    "checked": bool(probe),
                    "ok": api_reachable,
                    "error": api_error,
                },
                "jwt_enabled": self._onlyoffice_jwt_enabled(),
                "jwt_configured": bool(self._onlyoffice_jwt_enabled() and str(getattr(self.server.config, "onlyoffice_jwt_secret", "") or "").strip()),
                "files_count": files_count,
            })
        except Exception as exc:
            self.server.signal("api-onlyoffice-error", route="status", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_onlyoffice_config(self) -> None:
        try:
            body = self._read_json()
            path = self._onlyoffice_safe_path(str(body.get("path", "") or ""))
            callback_base = self._onlyoffice_callback_base_url()
            public_url = self._onlyoffice_browser_document_server_url()
            file_url = f"{callback_base}/api/applications/onlyoffice/file?{urlencode({'path': self._onlyoffice_relative_path(path)})}"
            callback_url = f"{callback_base}/api/applications/onlyoffice/callback?{urlencode({'path': self._onlyoffice_relative_path(path)})}"
            title = path.name
            document_key = self._onlyoffice_document_key(path)
            config: dict[str, Any] = {
                "documentType": "cell",
                "type": "desktop",
                "width": "100%",
                "height": "100%",
                "document": {
                    "title": title,
                    "url": file_url,
                    "fileType": "xlsx",
                    "key": document_key,
                    "permissions": {
                        "download": True,
                        "edit": True,
                        "print": True,
                        "review": False,
                    },
                },
                "editorConfig": {
                    "mode": "edit",
                    "callbackUrl": callback_url,
                    "lang": "en",
                    "customization": {
                        "autosave": True,
                        "forcesave": True,
                    },
                    "user": {
                        "id": "main-computer-local-user",
                        "name": "Main Computer",
                    },
                },
            }
            self._onlyoffice_attach_token(config)
            status = self._onlyoffice_status_payload(probe=False)
            browser_public_url_candidates = status.get("browser_public_url_candidates", self._onlyoffice_browser_public_url_candidates())
            self.server.signal("api-onlyoffice-config", path=path.name, key=document_key)
            self._send_json(
                {
                    "ok": True,
                    "enabled": bool(getattr(self.server.config, "onlyoffice_enabled", False)),
                    "document_server_url": public_url,
                    "public_url": public_url,
                    "configured_public_url": self._onlyoffice_public_url(),
                    "browser_public_url_override": self._onlyoffice_browser_public_url_override(),
                    "internal_url": self._onlyoffice_internal_url(),
                    "callback_base_url": callback_base,
                    "browser_public_url_candidates": browser_public_url_candidates,
                    "config": config,
                    "server": status,
                    **self._onlyoffice_file_payload(path),
                }
            )
        except Exception as exc:
            self.server.signal("api-onlyoffice-error", route="config", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_onlyoffice_file_get(self) -> None:
        try:
            query = parse_qs(urlsplit(self.path).query)
            raw_path = str(query.get("path", [""])[0] or "")
            path = self._onlyoffice_safe_path(raw_path)
            data = path.read_bytes()
            content_type = mimetypes.guess_type(path.name)[0] or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
            self.end_headers()
            self.wfile.write(data)
            self.server.signal("api-onlyoffice-file", path=path.name, bytes=len(data))
        except Exception as exc:
            self.server.signal("api-onlyoffice-error", route="file", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_onlyoffice_callback(self) -> None:
        try:
            query = parse_qs(urlsplit(self.path).query)
            raw_path = str(query.get("path", [""])[0] or "")
            path = self._onlyoffice_safe_path(raw_path, must_exist=False)
            body = self._read_json()
            status = int(body.get("status", 0) or 0)
            saved = False
            if status in {2, 6}:
                edited_url = str(body.get("url") or "").strip()
                if not edited_url:
                    raise ValueError("ONLYOFFICE callback did not include an edited document URL.")
                request = Request(edited_url, headers={"User-Agent": "main-computer-onlyoffice"})
                with urlopen(request, timeout=60) as response:
                    data = response.read()
                if not self._onlyoffice_looks_like_xlsx(data):
                    raise ValueError("ONLYOFFICE callback URL did not return an .xlsx package.")
                self._onlyoffice_atomic_write(path, data)
                saved = True
            self.server.signal("api-onlyoffice-callback", path=path.name, status=status, saved=saved)
            self._send_json({"error": 0})
        except Exception as exc:
            self.server.signal("api-onlyoffice-error", route="callback", error=exc)
            self._send_json({"error": 1, "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_onlyoffice_force_save(self) -> None:
        try:
            body = self._read_json()
            path = self._onlyoffice_safe_path(str(body.get("path", "") or ""))
            command = {
                "c": "forcesave",
                "key": self._onlyoffice_document_key(path),
            }
            self._onlyoffice_attach_token(command)
            endpoint = f"{self._onlyoffice_internal_url()}/coauthoring/CommandService.ashx"
            request = Request(
                endpoint,
                data=json.dumps(command).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8", errors="replace")
            try:
                payload: object = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"raw": raw}
            self.server.signal("api-onlyoffice-force-save", path=path.name)
            self._send_json({"ok": True, "command_response": payload})
        except Exception as exc:
            self.server.signal("api-onlyoffice-error", route="force-save", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _onlyoffice_effective_mode(self) -> str:
        configured = str(getattr(self.server.config, "onlyoffice_mode", "") or "").strip().lower()
        if configured in {"docker", "external", "disabled"}:
            return configured
        return "docker"

    def _onlyoffice_public_url(self) -> str:
        public_url = str(getattr(self.server.config, "onlyoffice_public_url", "") or "").strip().rstrip("/")
        legacy_url = str(getattr(self.server.config, "onlyoffice_document_server_url", "") or "").strip().rstrip("/")
        return public_url or legacy_url or "http://127.0.0.1:18085"

    def _onlyoffice_browser_public_url_override(self) -> str:
        """Return an optional browser-facing ONLYOFFICE Docs URL override."""

        return str(getattr(self.server.config, "onlyoffice_browser_public_url", "") or "").strip().rstrip("/")

    def _onlyoffice_browser_document_server_url(self) -> str:
        return self._onlyoffice_browser_public_url_override() or self._onlyoffice_public_url()

    def _onlyoffice_browser_public_url_candidates(self) -> list[str]:
        """Return browser-side Document Server URL candidates for local Docker mode."""

        candidates: list[str] = []

        def add(value: str | None) -> None:
            clean = str(value or "").strip().rstrip("/")
            if clean and clean not in candidates:
                candidates.append(clean)

        def add_loopback_aliases(value: str | None) -> None:
            clean = str(value or "").strip().rstrip("/")
            if not clean:
                return
            add(clean)
            try:
                parsed = urlsplit(clean)
                port = parsed.port
            except Exception:
                return

            if parsed.scheme not in {"http", "https"}:
                return
            if (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
                return

            if port is None:
                port = 443 if parsed.scheme == "https" else 80
            path = (parsed.path or "").rstrip("/")
            for host in ("127.0.0.1", "localhost"):
                netloc = f"{host}:{port}" if port else host
                add(urlunsplit((parsed.scheme, netloc, path, "", "")))

        add_loopback_aliases(self._onlyoffice_browser_public_url_override())
        add_loopback_aliases(self._onlyoffice_public_url())
        add_loopback_aliases(self._onlyoffice_internal_url())
        add_loopback_aliases("http://127.0.0.1:18085")
        add_loopback_aliases("http://localhost:18085")
        return candidates

    def _onlyoffice_internal_url(self) -> str:
        value = str(getattr(self.server.config, "onlyoffice_internal_url", "") or "").strip().rstrip("/")
        if value:
            return value
        return self._onlyoffice_public_url()

    def _onlyoffice_document_server_url(self) -> str:
        # Backward-compatible helper name used by the first ONLYOFFICE spike.
        return self._onlyoffice_public_url()

    def _onlyoffice_callback_base_url(self) -> str:
        configured = str(getattr(self.server.config, "onlyoffice_callback_base_url", "") or "").strip().rstrip("/")
        if not configured:
            configured = str(getattr(self.server.config, "onlyoffice_public_base_url", "") or "").strip().rstrip("/")
        if configured:
            return configured

        if self._onlyoffice_effective_mode() == "docker":
            return f"http://host.docker.internal:{self.server.server_port}"

        host = str(self.headers.get("Host") or f"127.0.0.1:{self.server.server_port}").strip()
        scheme = "https" if self.headers.get("X-Forwarded-Proto", "").lower() == "https" else "http"
        return f"{scheme}://{host}"

    def _onlyoffice_public_base_url(self) -> str:
        # Backward-compatible helper name; this is the URL ONLYOFFICE uses to
        # call back to Main Computer, not the Document Server URL.
        return self._onlyoffice_callback_base_url()

    def _onlyoffice_probe_url(self, url: str, *, timeout_s: float = 0.75) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            request = Request(url, headers={"User-Agent": "main-computer-onlyoffice-status"})
            with urlopen(request, timeout=timeout_s) as response:
                response.read(256)
                elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
                return {"ok": True, "url": url, "status": response.status, "elapsed_ms": elapsed_ms, "error": ""}
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            return {"ok": False, "url": url, "status": None, "elapsed_ms": elapsed_ms, "error": str(exc)}

    def _onlyoffice_status_payload(self, *, probe: bool = True) -> dict[str, Any]:
        configured_public_url = self._onlyoffice_public_url()
        browser_public_url_override = self._onlyoffice_browser_public_url_override()
        public_url = self._onlyoffice_browser_document_server_url()
        internal_url = self._onlyoffice_internal_url()
        callback_base_url = self._onlyoffice_callback_base_url()
        browser_public_url_candidates = self._onlyoffice_browser_public_url_candidates()
        public_api_url = f"{public_url}/web-apps/apps/api/documents/api.js"
        internal_api_url = f"{internal_url}/web-apps/apps/api/documents/api.js"
        server_probe = self._onlyoffice_probe_url(public_api_url) if probe else {"ok": None, "url": public_api_url, "status": None, "elapsed_ms": None, "error": ""}
        internal_probe = (
            self._onlyoffice_probe_url(internal_api_url)
            if probe and internal_url != public_url
            else server_probe
        )
        return {
            "mode": self._onlyoffice_effective_mode(),
            "enabled": bool(getattr(self.server.config, "onlyoffice_enabled", False)),
            "configured_public_url": configured_public_url,
            "browser_public_url_override": browser_public_url_override,
            "public_url": public_url,
            "internal_url": internal_url,
            "callback_base_url": callback_base_url,
            "document_server_url": public_url,
            "browser_public_url_candidates": browser_public_url_candidates,
            "public_api_url": public_api_url,
            "internal_api_url": internal_api_url,
            "server_probe": server_probe,
            "internal_probe": internal_probe,
            "storage_root": str(self._onlyoffice_root()),
            "jwt_enabled": self._onlyoffice_jwt_enabled(),
            "jwt_configured": bool(self._onlyoffice_jwt_enabled() and str(getattr(self.server.config, "onlyoffice_jwt_secret", "") or "").strip()),
        }

    def _onlyoffice_root(self) -> Path:
        configured = getattr(self.server.config, "onlyoffice_storage_root", Path("runtime/onlyoffice/workbooks"))
        root = configured if isinstance(configured, Path) else Path(str(configured))
        if not root.is_absolute():
            root = self.server.debug_root / root
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()

    def _onlyoffice_safe_path(self, raw_path: str, must_exist: bool = True) -> Path:
        raw_path = str(raw_path or "").replace("\\", "/").strip()
        if not raw_path:
            raise ValueError("ONLYOFFICE path is required.")
        if raw_path.startswith("/") or re.match(r"^[A-Za-z]:", raw_path):
            raise ValueError("Absolute ONLYOFFICE paths are not allowed.")
        normalized = posixpath.normpath(raw_path)
        parts = [part for part in normalized.split("/") if part and part != "."]
        if not parts or any(part == ".." or part.startswith(".") for part in parts):
            raise ValueError("Unsafe ONLYOFFICE path.")
        root = self._onlyoffice_root()
        candidate = (root / Path(*parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("ONLYOFFICE path escapes storage root.") from exc
        if candidate.suffix.lower() != ".xlsx":
            raise ValueError("ONLYOFFICE workbooks must be .xlsx files.")
        if must_exist and not candidate.is_file():
            raise ValueError("ONLYOFFICE workbook not found.")
        return candidate

    def _onlyoffice_relative_path(self, path: Path) -> str:
        return path.resolve().relative_to(self._onlyoffice_root()).as_posix()

    def _onlyoffice_content_hash(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _onlyoffice_file_payload(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        relative_path = self._onlyoffice_relative_path(path)
        return {
            "path": relative_path,
            "display_path": f"onlyoffice/{relative_path}",
            "kind": "xlsx",
            "bytes": stat.st_size,
            "mtime": stat.st_mtime,
            "content_hash": self._onlyoffice_content_hash(path),
        }

    def _onlyoffice_document_key(self, path: Path) -> str:
        relative_path = self._onlyoffice_relative_path(path)
        stat = path.stat() if path.exists() else None
        seed = f"{relative_path}:{stat.st_mtime_ns if stat else 0}:{stat.st_size if stat else 0}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]

    def _onlyoffice_atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent, prefix=f".{path.name}.", suffix=".tmp") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, path)

    def _onlyoffice_looks_like_xlsx(self, data: bytes) -> bool:
        if not data.startswith(b"PK"):
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return False
        return "[Content_Types].xml" in names and "xl/workbook.xml" in names

    def _onlyoffice_blank_xlsx(self) -> bytes:
        workbook = {
            "version": 1,
            "active_sheet": "Sheet1",
            "sheets": {
                "Sheet1": {
                    "rows": 50,
                    "cols": 26,
                    "cells": {
                        "A1": {"value": "ONLYOFFICE workbook"},
                        "B1": {"value": "42"},
                    },
                }
            },
            "metadata": {},
        }
        return self._spreadsheet_xlsx_export_workbook(workbook)

    def _onlyoffice_jwt_enabled(self) -> bool:
        return bool(getattr(self.server.config, "onlyoffice_jwt_enabled", True))

    def _onlyoffice_attach_token(self, payload: dict[str, Any]) -> None:
        if not self._onlyoffice_jwt_enabled():
            return
        secret = str(getattr(self.server.config, "onlyoffice_jwt_secret", "") or "").strip()
        if not secret:
            return
        payload["token"] = self._onlyoffice_jwt_encode(payload, secret)

    def _onlyoffice_jwt_encode(self, payload: dict[str, Any], secret: str) -> str:
        header = {"alg": "HS256", "typ": "JWT"}

        def b64url(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

        header_part = b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        payload_part = b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        return f"{header_part}.{payload_part}.{b64url(signature)}"
