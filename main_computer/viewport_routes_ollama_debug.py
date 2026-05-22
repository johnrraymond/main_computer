from __future__ import annotations

from main_computer.viewport_state import *  # noqa: F401,F403

class ViewportOllamaDebugRoutesMixin:
    def _handle_ollama_debug_session(self) -> None:
        try:
            body = self._read_json()
            action = str(body.get("action", "enable")).strip().lower()
            if action not in {"enable", "disable"}:
                self._send_json({"error": "action must be enable or disable."}, status=HTTPStatus.BAD_REQUEST)
                return
            if action == "enable" and not self._debug_passcode_ok(body):
                self.server.signal("api-ollama-debug-rejected", reason="bad-passcode")
                self._send_json({"error": "Debug passcode is required."}, status=HTTPStatus.FORBIDDEN)
                return
            self.server.ollama_debug_active = action == "enable"
            self.server.signal("api-ollama-debug-session", active=self.server.ollama_debug_active)
            self._send_json(self._debug_status())
        except Exception as exc:
            self.server.signal("api-ollama-debug-session-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_ollama_debug_chat(self) -> None:
        try:
            body = self._read_json()
            if not self._debug_ready(body):
                return
            prompt = str(body.get("prompt", "")).strip()
            if not prompt:
                self._send_json({"error": "Prompt is required."}, status=HTTPStatus.BAD_REQUEST)
                return
            model = str(body.get("model") or self.server.config.model or "gemma4:26b").strip()
            provider_class = viewport_ollama_provider_class()
            provider = provider_class(
                model=model,
                base_url=self.server.config.ollama_base_url,
                timeout_s=self.server.config.ollama_timeout_s,
            )
            context_pack = self.server.computer.catalog.build_context_pack(prompt)
            system = (
                "You are the Main Computer Ollama debug mode. You are allowed to inspect, explain, "
                "and propose edits for the local main_computer project. Use the provided workspace "
                "map as grounding context. If you need exact file contents, ask the user to use the "
                "debug read action for a specific project-relative path. When changing code, return "
                "complete replacement text for the file or a precise patch-sized instruction."
            )
            context = (
                "Current deterministic workspace context available to debug mode:\n"
                f"{context_pack.text}\n\n"
                f"Debug project root: {self.server.debug_root}\n"
                "Debug paths are project-root relative. Root files like TODO.md and README.md do not need "
                "main_computer/../ prefixes.\n"
                "Important main computer folders: main_computer, main_computer_test, main_copmputer_production"
            )
            self.server.signal("api-ollama-debug-chat-start", model=model, prompt_chars=len(prompt))
            self.server.signal(
                "api-ollama-debug-context-selected",
                evidence_count=len(context_pack.evidence),
                manifest_chars=context_pack.manifest_chars,
                paths="|".join(self._context_evidence_paths(context_pack)),
                files="|".join(self._context_evidence_paths(context_pack, kind="file")),
            )
            response = provider.chat(
                [
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="system", content=context),
                    ChatMessage(role="user", content=prompt),
                ]
            )
            self.server.signal("api-ollama-debug-chat-complete", model=model, response_chars=len(response.content))
            self._send_json(asdict(response))
        except Exception as exc:
            self.server.signal("api-ollama-debug-chat-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)

    def _context_evidence_paths(self, context_pack: Any, *, kind: str | None = None) -> list[str]:
        paths: list[str] = []
        for item in getattr(context_pack, "evidence", ()):
            if kind is not None and getattr(item, "kind", "") != kind:
                continue
            path = str(getattr(item, "path", ""))
            if path and path not in paths:
                paths.append(path)
            if len(paths) >= 12:
                break
        return paths

    def _handle_ollama_debug_read(self) -> None:
        try:
            body = self._read_json()
            if not self._debug_ready(body):
                return
            path = self._debug_path(str(body.get("path", "")))
            content = path.read_text(encoding="utf-8")
            self.server.signal("api-ollama-debug-read", path=path, chars=len(content))
            self._send_json({"path": self._relative_debug_path(path), "content": content})
        except Exception as exc:
            self.server.signal("api-ollama-debug-read-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_ollama_debug_write(self) -> None:
        try:
            body = self._read_json()
            if not self._debug_ready(body):
                return
            path = self._debug_path(str(body.get("path", "")), must_exist=False)
            content = str(body.get("content", ""))
            if len(content.encode("utf-8")) > 2_000_000:
                self._send_json({"error": "Debug writes are limited to 2 MB."}, status=HTTPStatus.BAD_REQUEST)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            snapshot = self.server.revisions.snapshot_before_write(path, "debug write")
            path.write_text(content, encoding="utf-8")
            self.server.signal("api-ollama-debug-write", path=path, bytes=len(content.encode("utf-8")))
            self._send_json(
                {
                    "path": self._relative_debug_path(path),
                    "bytes": len(content.encode("utf-8")),
                    "snapshot": snapshot.get("created") if snapshot else None,
                }
            )
        except Exception as exc:
            self.server.signal("api-ollama-debug-write-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_ollama_debug_revise(self) -> None:
        try:
            body = self._read_json()
            if not self._debug_ready(body):
                return
            path = self._debug_path(str(body.get("path", "")))
            instruction = str(body.get("instruction", "")).strip()
            if not instruction:
                self._send_json({"error": "Revision instruction is required."}, status=HTTPStatus.BAD_REQUEST)
                return
            current = path.read_text(encoding="utf-8")
            if len(current.encode("utf-8")) > 250_000:
                self._send_json({"error": "Revision input is limited to 250 KB."}, status=HTTPStatus.BAD_REQUEST)
                return
            model = str(body.get("model") or self.server.config.model or "gemma4:26b").strip()
            provider_class = viewport_ollama_provider_class()
            provider = provider_class(
                model=model,
                base_url=self.server.config.ollama_base_url,
                timeout_s=self.server.config.ollama_timeout_s,
            )
            system = (
                "You are the Main Computer Ollama debug self-editor. Return only the complete replacement "
                "content for the requested file. Do not explain the change. Do not wrap the answer in markdown."
            )
            prompt = (
                f"Project file: {self._relative_debug_path(path)}\n"
                f"Instruction: {instruction}\n\n"
                "Current file content:\n"
                f"{current}"
            )
            self.server.signal("api-ollama-debug-revise-start", model=model, path=path, chars=len(current))
            response = provider.chat(
                [
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=prompt),
                ]
            )
            replacement = self._extract_debug_replacement(response.content)
            if not replacement.strip():
                self._send_json({"error": "Model returned an empty replacement."}, status=HTTPStatus.BAD_GATEWAY)
                return
            if len(replacement.encode("utf-8")) > 2_000_000:
                self._send_json({"error": "Model replacement is limited to 2 MB."}, status=HTTPStatus.BAD_GATEWAY)
                return
            snapshot = self.server.revisions.snapshot_before_write(path, "debug revise")
            path.write_text(replacement, encoding="utf-8")
            self.server.signal("api-ollama-debug-revise-complete", path=path, bytes=len(replacement.encode("utf-8")))
            self._send_json(
                {
                    "path": self._relative_debug_path(path),
                    "bytes": len(replacement.encode("utf-8")),
                    "model": model,
                    "snapshot": snapshot.get("created") if snapshot else None,
                }
            )
        except Exception as exc:
            self.server.signal("api-ollama-debug-revise-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _debug_status(self) -> dict[str, Any]:
        return {
            "active": self.server.ollama_debug_active,
            "provider": "ollama",
            "model": self.server.config.model or "gemma4:26b",
            "patch_level": self.server.config.patch_level,
            "ollama_base_url": self.server.config.ollama_base_url,
            "ollama_timeout_s": self.server.config.ollama_timeout_s,
            "passcode_required": bool(self.server.config.ollama_debug_passcode),
            "root": str(self.server.debug_root),
            "assets_root": str(self.server.debug_assets_root),
            "can_self_edit": True,
        }

    def _debug_passcode_ok(self, body: dict[str, Any]) -> bool:
        required = self.server.config.ollama_debug_passcode
        if not required:
            return True
        supplied = str(body.get("passcode") or self.headers.get("X-Main-Computer-Debug-Passcode") or "")
        return supplied == required

    def _debug_ready(self, body: dict[str, Any]) -> bool:
        if not self.server.ollama_debug_active:
            self._send_json({"error": "Ollama debug mode is disabled. Enable it first."}, status=HTTPStatus.FORBIDDEN)
            return False
        if not self._debug_passcode_ok(body):
            self._send_json({"error": "Debug passcode is required."}, status=HTTPStatus.FORBIDDEN)
            return False
        return True
