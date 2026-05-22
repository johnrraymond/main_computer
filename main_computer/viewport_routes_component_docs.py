from __future__ import annotations

import html

from main_computer.viewport_state import *  # noqa: F401,F403


class ViewportComponentDocsRoutesMixin:
    def _component_docs_root(self) -> Path:
        return (self.server.debug_root / "generated_component_docs").resolve()

    def _component_docs_manifest_path(self) -> Path:
        return self._component_docs_root() / "manifest.json"

    def _component_docs_snap_config_path(self) -> Path:
        return (self.server.debug_root / "main_computer" / "config" / "code_editor_viewport_snap.json").resolve()

    def _component_docs_safe_parts(self, requested: str) -> list[str]:
        raw = str(requested or "").replace("\\", "/").strip()
        candidate = Path(raw)
        if candidate.is_absolute() or raw.startswith("/"):
            raise ValueError("Component documentation paths must be relative.")
        parts = [part for part in raw.split("/") if part and part != "."]
        if parts and parts[0] == "generated_component_docs":
            parts = parts[1:]
        if not parts:
            raise ValueError("Component documentation path is required.")
        if any(part == ".." for part in parts):
            raise ValueError("Component documentation paths may not contain traversal.")
        return parts

    def _component_docs_path(self, requested: str) -> Path:
        root = self._component_docs_root()
        parts = self._component_docs_safe_parts(requested)
        candidate = (root.joinpath(*parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Component documentation paths must stay inside generated_component_docs.") from exc
        if candidate.suffix.lower() not in {".html", ".json"}:
            raise ValueError("Component documentation serves only .html and .json files.")
        if not candidate.is_file():
            raise ValueError("Component documentation file does not exist.")
        return candidate

    def _component_docs_manifest(self) -> dict[str, Any]:
        path = self._component_docs_manifest_path()
        if not path.exists():
            return {"schema_version": 1, "entries": []}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"schema_version": 1, "entries": []}
        entries = payload.get("entries")
        if not isinstance(entries, list):
            payload["entries"] = []
        payload.setdefault("schema_version", 1)
        return payload

    def _component_docs_manifest_entry(self, target_id: str) -> dict[str, Any] | None:
        target = str(target_id or "").strip()
        for entry in self._component_docs_manifest().get("entries", []):
            if not isinstance(entry, dict):
                continue
            aliases = [str(alias) for alias in entry.get("aliases", []) if alias]
            if str(entry.get("id") or "") == target or target in aliases:
                return entry
        return None

    def _handle_component_docs_manifest(self) -> None:
        try:
            self._read_json()
            manifest = self._component_docs_manifest()
            self.server.signal("api-component-docs-manifest", count=len(manifest.get("entries", [])))
            self._send_json({"ok": True, "root": "generated_component_docs", **manifest})
        except Exception as exc:
            self.server.signal("api-component-docs-error", route="manifest", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_component_docs_read(self) -> None:
        try:
            body = self._read_json()
            target_id = str(body.get("id") or "").strip()
            requested_path = str(body.get("path") or "").strip()
            entry: dict[str, Any] | None = None
            if target_id:
                entry = self._component_docs_manifest_entry(target_id)
                canonical_id = str(entry.get("id") or target_id) if entry else target_id
                requested_path = str(entry.get("doc_path") or "") if entry else f"nodes/{canonical_id}.html"
            if not requested_path:
                raise ValueError("Component documentation id or path is required.")
            root = self._component_docs_root()
            parts = self._component_docs_safe_parts(requested_path)
            candidate = (root.joinpath(*parts)).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError("Component documentation paths must stay inside generated_component_docs.") from exc
            if target_id and not candidate.exists():
                response_id = str(entry.get("id") or target_id) if entry else target_id
                safe_target = html.escape(response_id)
                self.server.signal("api-component-docs-missing", id=response_id, path=requested_path)
                self._send_json(
                    {
                        "ok": True,
                        "exists": False,
                        "id": response_id,
                        "path": requested_path,
                        "display_path": f"generated_component_docs/{requested_path}",
                        "title": target_id,
                        "content": (
                            f'<article class="mc-component-doc" data-mc-doc-target="{safe_target}">'
                            "<h1>No generated documentation yet</h1>"
                            f"<p>No generated HTML documentation exists for <code>{safe_target}</code>.</p>"
                            "</article>"
                        ),
                        "content_type": "text/html",
                        "metadata": entry or {},
                    }
                )
                return
            path = self._component_docs_path(requested_path)
            relative_path = path.relative_to(self._component_docs_root()).as_posix()
            content = path.read_text(encoding="utf-8", errors="replace")
            stat = path.stat()
            content_type = "text/html" if path.suffix.lower() == ".html" else "application/json"
            self.server.signal("api-component-docs-read", path=relative_path, bytes=stat.st_size)
            self._send_json(
                {
                    "ok": True,
                    "exists": True,
                    "id": str((entry or {}).get("id") or target_id or ""),
                    "path": relative_path,
                    "display_path": f"generated_component_docs/{relative_path}",
                    "title": str((entry or {}).get("title") or (entry or {}).get("feature_description") or target_id or path.stem),
                    "content": content,
                    "content_type": content_type,
                    "bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "metadata": entry or {},
                }
            )
        except Exception as exc:
            self.server.signal("api-component-docs-error", route="read", error=exc)
            self._send_json({"ok": False, "exists": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_component_docs_viewport_config(self) -> None:
        try:
            self._read_json()
            path = self._component_docs_snap_config_path()
            config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            if not isinstance(config, dict):
                config = {}
            self.server.signal("api-component-docs-viewport-config")
            self._send_json({"ok": True, "config": config})
        except Exception as exc:
            self.server.signal("api-component-docs-error", route="viewport-config", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
