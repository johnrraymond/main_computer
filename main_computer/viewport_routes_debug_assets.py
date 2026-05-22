from __future__ import annotations

from main_computer.viewport_state import *  # noqa: F401,F403

class ViewportDebugAssetRoutesMixin:
    def _handle_debug_asset_write(self) -> None:
        try:
            body = self._read_json()
            if not self._debug_ready(body):
                return
            content = str(body.get("content", ""))
            kind = str(body.get("kind", "text")).strip() or "text"
            requested_name = str(body.get("name", "")).strip()
            auto_name = bool(body.get("auto_name", False)) or not requested_name
            if auto_name:
                name, name_source = self._generate_debug_asset_name(content, kind)
            else:
                name, name_source = requested_name, "requested"
            if len(content.encode("utf-8")) > 2_000_000:
                self._send_json({"error": "Debug assets are limited to 2 MB."}, status=HTTPStatus.BAD_REQUEST)
                return
            path = self._debug_asset_path(name, must_exist=False)
            path.parent.mkdir(parents=True, exist_ok=True)
            snapshot = self.server.debug_asset_revisions.snapshot_before_change("asset write")
            path.write_text(content, encoding="utf-8")
            self._write_debug_asset_manifest(path.name, kind)
            self.server.signal("api-debug-asset-write", asset_name=path.name, name_source=name_source, bytes=len(content.encode("utf-8")))
            self._send_json(
                {
                    "name": path.name,
                    "path": str(path),
                    "bytes": len(content.encode("utf-8")),
                    "kind": kind,
                    "name_source": name_source,
                    "snapshot": snapshot.get("created"),
                    "history": self.server.debug_asset_revisions.status(),
                }
            )
        except Exception as exc:
            self.server.signal("api-debug-asset-write-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_debug_asset_read(self) -> None:
        try:
            body = self._read_json()
            if not self._debug_ready(body):
                return
            path = self._debug_asset_path(str(body.get("name", "")))
            content = path.read_text(encoding="utf-8")
            self.server.signal("api-debug-asset-read", asset_name=path.name, chars=len(content))
            self._send_json({"name": path.name, "path": str(path), "content": content})
        except Exception as exc:
            self.server.signal("api-debug-asset-read-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_debug_asset_delete(self) -> None:
        try:
            body = self._read_json()
            if not self._debug_ready(body):
                return
            path = self._debug_asset_path(str(body.get("name", "")))
            snapshot = self.server.debug_asset_revisions.snapshot_before_change("asset delete")
            path.unlink()
            self.server.signal("api-debug-asset-delete", asset_name=path.name)
            self._send_json(
                {
                    "name": path.name,
                    "deleted": True,
                    "snapshot": snapshot.get("created"),
                    "assets": self._list_debug_assets(),
                    "history": self.server.debug_asset_revisions.status(),
                }
            )
        except Exception as exc:
            self.server.signal("api-debug-asset-delete-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_debug_asset_snapshot(self) -> None:
        try:
            body = self._read_json()
            if not self._debug_ready(body):
                return
            label = str(body.get("label", "manual asset checkpoint"))
            report = self.server.debug_asset_revisions.create_snapshot(label=label, reason="manual")
            self.server.signal("api-debug-asset-snapshot", snapshot_id=report.get("created", {}).get("id", ""))
            self._send_json(report)
        except Exception as exc:
            self.server.signal("api-debug-asset-snapshot-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_debug_asset_restore(self) -> None:
        try:
            body = self._read_json()
            if not self._debug_ready(body):
                return
            snapshot_id = str(body.get("id", ""))
            pre_restore = self.server.debug_asset_revisions.create_snapshot(
                label="before asset restore",
                reason="pre-restore",
            )
            report = self.server.debug_asset_revisions.restore(snapshot_id)
            self.server.signal("api-debug-asset-restore", snapshot_id=snapshot_id)
            self._send_json({**report, "pre_restore": pre_restore.get("created"), "assets": self._list_debug_assets()})
        except Exception as exc:
            self.server.signal("api-debug-asset-restore-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_debug_asset_reset(self) -> None:
        try:
            body = self._read_json()
            if not self._debug_ready(body):
                return
            label = str(body.get("label", "before asset reset"))
            report = self.server.debug_asset_revisions.reset(label=label)
            self.server.signal("api-debug-asset-reset")
            self._send_json({**report, "assets": self._list_debug_assets()})
        except Exception as exc:
            self.server.signal("api-debug-asset-reset-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _debug_path(self, raw_path: str, *, must_exist: bool = True) -> Path:
        if not raw_path.strip():
            raise ValueError("Project file path is required.")
        root = self.server.debug_root
        candidate = self._resolve_debug_path(root, raw_path, must_exist=must_exist)
        allowed_roots = self._debug_allowed_roots(root)
        if not any(candidate == allowed_root or allowed_root in candidate.parents for allowed_root in allowed_roots):
            raise ValueError("Debug file path must stay inside the running main computer project.")
        if "__pycache__" in candidate.parts:
            raise ValueError("Debug mode does not edit Python cache files.")
        if must_exist and not candidate.exists():
            raise FileNotFoundError(f"Debug file not found: {self._relative_debug_path(candidate)}")
        if candidate.exists() and candidate.is_dir():
            raise IsADirectoryError(f"Debug file is a directory: {self._relative_debug_path(candidate)}")
        return candidate

    def _resolve_debug_path(self, root: Path, raw_path: str, *, must_exist: bool) -> Path:
        candidate = (root / raw_path).resolve()
        if candidate.exists() or not must_exist:
            return candidate

        clean_parts = tuple(part for part in raw_path.replace("\\", "/").split("/") if part and part != ".")
        if len(clean_parts) > 1 and clean_parts[0] == "main_computer":
            project_root_candidate = (root / Path(*clean_parts[1:])).resolve()
            if project_root_candidate.exists():
                return project_root_candidate
        if clean_parts and clean_parts[0] in PRIORITY_PROJECTS:
            workspace_candidate = (self.server.config.workspace / Path(*clean_parts)).resolve()
            if workspace_candidate.exists():
                return workspace_candidate
        return candidate

    def _debug_allowed_roots(self, root: Path) -> list[Path]:
        roots = [root.resolve()]
        workspace = self.server.config.workspace.resolve()
        for project_name in PRIORITY_PROJECTS:
            project_root = (workspace / project_name).resolve()
            if project_root.exists():
                roots.append(project_root)
        return roots

    def _relative_debug_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.server.debug_root).as_posix()
        except ValueError:
            try:
                return path.resolve().relative_to(self.server.config.workspace.resolve()).as_posix()
            except ValueError:
                return str(path)

    def _debug_asset_path(self, raw_name: str, *, must_exist: bool = True) -> Path:
        if not raw_name.strip():
            raise ValueError("Debug asset name is required.")
        clean = raw_name.replace("\\", "/").split("/")[-1].strip()
        clean = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in clean)
        if not clean:
            raise ValueError("Debug asset name is empty after sanitizing.")
        if "." not in clean:
            clean = f"{clean}.txt"
        root = self.server.debug_assets_root.resolve()
        candidate = (root / clean).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("Debug asset path must stay inside debug_assets.")
        if must_exist and not candidate.exists():
            raise FileNotFoundError(f"Debug asset not found: {clean}")
        return candidate

    def _generate_debug_asset_name(self, content: str, kind: str) -> tuple[str, str]:
        ollama_name = self._generate_debug_asset_name_with_ollama(content, kind)
        if ollama_name:
            return self._unique_debug_asset_name(ollama_name), "ollama"

        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        seed = f"{stamp}:{kind}:{len(content)}:{content[:80]}"
        suffix = "".join(ch for ch in seed.lower() if ch.isalnum())[-8:] or "artifact"
        return self._unique_debug_asset_name(f"debug-artifact-{stamp}-{suffix}.txt"), "fallback"

    def _generate_debug_asset_name_with_ollama(self, content: str, kind: str) -> str:
        excerpt = content.strip().replace("\r\n", "\n")[:1200]
        if not excerpt:
            excerpt = "[empty debug asset]"
        try:
            provider_class = viewport_ollama_provider_class()
            provider = provider_class(
                model=self.server.config.model,
                base_url=self.server.config.ollama_base_url,
                timeout_s=min(self.server.config.ollama_timeout_s, 20.0),
                options={"num_predict": 24, "temperature": 0.1},
                think=False,
            )
            response = provider.chat(
                [
                    ChatMessage(
                        role="system",
                        content=(
                            "Name a saved debug artifact. Return exactly one filename, no markdown, no quotes. "
                            "Use lowercase letters, numbers, hyphens, or underscores. End with .txt."
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=f"Artifact kind: {kind}\nArtifact content excerpt:\n{excerpt}",
                    ),
                ]
            )
            raw_name = response.content.strip().splitlines()[0] if response.content.strip() else ""
            clean = self._sanitize_debug_asset_name(raw_name)
            if clean:
                self.server.signal("api-debug-asset-name-ollama", raw=raw_name, clean=clean)
                return clean
        except Exception as exc:
            self.server.signal("api-debug-asset-name-fallback", error=exc)
        return ""

    def _unique_debug_asset_name(self, raw_name: str) -> str:
        candidate = self._debug_asset_path(raw_name, must_exist=False)
        if not candidate.exists():
            return candidate.name
        stem = candidate.stem
        suffix = candidate.suffix or ".txt"
        index = 2
        while True:
            next_candidate = self._debug_asset_path(f"{stem}-{index}{suffix}", must_exist=False)
            if not next_candidate.exists():
                return next_candidate.name
            index += 1

    def _sanitize_debug_asset_name(self, raw_name: str) -> str:
        clean = raw_name.replace("\\", "/").split("/")[-1].strip().lower()
        clean = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in clean)
        clean = clean.strip("._-")
        if not clean:
            return ""
        if "." not in clean:
            clean = f"{clean}.txt"
        return clean

    def _list_debug_assets(self) -> list[dict[str, Any]]:
        root = self.server.debug_assets_root
        if not root.exists():
            return []
        manifest = self._read_debug_asset_manifest()
        assets = []
        for path in sorted(root.iterdir(), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
            if not path.is_file() or path.name == "manifest.json":
                continue
            stat = path.stat()
            assets.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "kind": manifest.get(path.name, {}).get("kind", "text"),
                }
            )
        return assets

    def _read_debug_asset_manifest(self) -> dict[str, Any]:
        manifest_path = self.server.debug_assets_root / "manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_debug_asset_manifest(self, name: str, kind: str) -> None:
        root = self.server.debug_assets_root
        root.mkdir(parents=True, exist_ok=True)
        manifest = self._read_debug_asset_manifest()
        manifest[name] = {"kind": kind, "updated_at": datetime.now(tz=timezone.utc).isoformat()}
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def _extract_debug_replacement(self, content: str) -> str:
        stripped = content.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3:
                return "\n".join(lines[1:-1]) + "\n"
        return content
