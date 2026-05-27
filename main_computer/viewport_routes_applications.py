from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from main_computer.blog_install import (
    BlogInstallError,
    blog_install_assumptions,
    install_blog_layer,
    persist_blog_intent,
)
from main_computer.deployment_controllers import (
    DeploymentControllerError,
    load_deployment_controller_registry,
    upsert_deployment_controller,
)
from main_computer.website_project_manifest import (
    WebsiteProjectError,
    archive_website_project,
    create_local_platform_website_project,
    create_website_project,
    list_website_projects,
    load_website_project,
    publish_website,
    read_website_project_files,
    save_website_directus_connection,
    save_website_project_files,
    save_website_publish_target,
    validate_site_id,
)

from main_computer.viewport_state import *  # noqa: F401,F403
from main_computer.chat_ai_subprocess import append_text_log, config_to_payload
from main_computer.models import ChatResponse
from main_computer.rag_website_builder_real_edit_smoke import (
    build_evidence as build_website_builder_rag_evidence,
    discover_builder_allowlist as discover_website_builder_rag_allowlist,
    materialize_proposal as materialize_website_builder_rag_proposal,
    proposal_prompt as website_builder_rag_proposal_prompt,
    write_materialized_bundle as write_website_builder_rag_bundle,
)


def _mounted_editor_should_inline_test_provider(provider: Any) -> bool:
    module = str(getattr(getattr(provider, "__class__", None), "__module__", "") or "")
    if not provider or module.startswith("main_computer.providers"):
        return False
    return os.environ.get("MAIN_COMPUTER_DISABLE_INLINE_TEST_PROVIDER", "").strip().lower() not in {"1", "true", "yes", "on"}


def _mounted_editor_scope_query(source: str) -> bool:
    text = re.sub(r"\s+", " ", str(source or "").strip().lower())
    return (
        "what files can you see" in text
        or "which files can you see" in text
        or "what can you see" in text
        or text in {"scope", "show scope", "show me the scope"}
        or ("visible" in text and "files" in text)
    )


def _mounted_editor_edit_request(source: str) -> bool:
    text = re.sub(r"\s+", " ", str(source or "").strip().lower())
    if not text or _mounted_editor_scope_query(text):
        return False
    edit_verbs = {
        "add",
        "adjust",
        "change",
        "create",
        "decrease",
        "delete",
        "edit",
        "fix",
        "generate",
        "increase",
        "insert",
        "make",
        "modify",
        "move",
        "remove",
        "rename",
        "replace",
        "set",
        "tune",
        "update",
    }
    if re.search(r"\b(" + "|".join(sorted(edit_verbs)) + r")\b", text):
        return True
    return bool(re.search(r"\b(higher|lower|faster|slower|brighter|darker|bigger|smaller|stronger|weaker)\b", text))


def _website_publish_nested_error(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("error", "message"):
        text = str(value.get(key) or "").strip()
        if text:
            return text
    blog = value.get("blog")
    if isinstance(blog, dict):
        for key in ("error", "message"):
            text = str(blog.get(key) or "").strip()
            if text:
                return text
    payload = value.get("payload")
    if isinstance(payload, dict):
        text = _website_publish_nested_error(payload)
        if text:
            return text
    body = str(value.get("body") or "").strip()
    if body:
        return body
    return ""


def _website_publish_error_message(result: object) -> str:
    if not isinstance(result, dict):
        return "Website publish failed."
    for key in (
        "error",
        "blog_runtime_verify_error",
        "cms_verify_error",
        "verify_error",
        "publish_metadata_error",
    ):
        value = result.get(key)
        if value:
            return str(value)
    for key in ("blog_runtime_verify", "verify_payload", "blog_hydration"):
        text = _website_publish_nested_error(result.get(key))
        if text:
            return text
    cms_results = result.get("cms_verify")
    if isinstance(cms_results, list):
        for item in cms_results:
            if isinstance(item, dict) and not item.get("ok"):
                service = item.get("service") or "CMS dependency"
                error = item.get("error") or item.get("body") or item.get("status") or "verification failed"
                return f"{service}: {error}"
    returncode = result.get("returncode")
    if returncode not in (None, 0):
        stderr = str(result.get("stderr") or "").strip()
        if stderr:
            return stderr.splitlines()[-1]
        return f"Docker compose exited with code {returncode}."
    if result.get("verified") is False:
        return "Website publish finished, but verification failed."
    return "Website publish failed."





class ViewportApplicationRoutesMixin:

    def _handle_deployment_controllers(self) -> None:
        try:
            registry = load_deployment_controller_registry(self.server.debug_root)
            self.server.signal("api-deployment-controllers", count=len(registry.controllers))
            self._send_json({"ok": True, **registry.to_dict()})
        except DeploymentControllerError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-deployment-controllers-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_deployment_controller_save(self) -> None:
        try:
            body = self._read_json()
            registry = upsert_deployment_controller(
                self.server.debug_root,
                {
                    "id": body.get("id"),
                    "kind": body.get("kind") or "coolify",
                    "name": body.get("name"),
                    "base_url": body.get("base_url"),
                    "token_ref": body.get("token_ref"),
                    "roles": body.get("roles") or ["remote-prod"],
                    "default_for": body.get("default_for") or [],
                    "local": bool(body.get("local")),
                },
            )
            self.server.signal("api-deployment-controller-save", controller_id=body.get("id"))
            self._send_json({"ok": True, **registry.to_dict()})
        except DeploymentControllerError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-deployment-controller-save-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


    def _handle_websites_site_publish_target(self) -> None:
        try:
            body = self._read_json()
            controller_id = body.get("controller_id")
            controller_base_url = body.get("controller_base_url") or body.get("base_url")
            token_ref = body.get("token_ref")
            if controller_id and controller_base_url and token_ref:
                upsert_deployment_controller(
                    self.server.debug_root,
                    {
                        "id": controller_id,
                        "kind": "coolify",
                        "name": body.get("controller_name") or controller_id,
                        "base_url": controller_base_url,
                        "token_ref": token_ref,
                        "roles": ["remote-prod"],
                        "default_for": ["remote-prod"],
                        "local": str(controller_id).strip().lower() == "coolify-local",
                    },
                )
            raw_publish_directus_url = body.get("publish_directus_url") if "publish_directus_url" in body else body.get("directus_url")
            publish_directus_url = raw_publish_directus_url if str(raw_publish_directus_url or "").strip() else None
            project = save_website_publish_target(
                self.server.debug_root,
                body.get("site_id") or body.get("id"),
                body.get("lane") or body.get("publish_lane") or "remote_prod",
                controller_id=controller_id,
                project=body.get("project"),
                environment=body.get("environment"),
                domain=body.get("domain"),
                publish_mode=body.get("publish_mode"),
                use_local_server=body.get("use_local_server"),
                site_slug=body.get("site_slug") or body.get("project"),
                source_path=body.get("source_path"),
                remote_host=body.get("remote_host") or body.get("ssh_host"),
                remote_root=body.get("remote_root"),
                ssh_password=body.get("ssh_password"),
                resource_uuid=body.get("resource_uuid"),
                service_uuid=body.get("service_uuid"),
                application_uuid=body.get("application_uuid"),
                uuid=body.get("uuid"),
                publish_directus_url=publish_directus_url,
            )
            payload = read_website_project_files(self.server.debug_root, project.id)
            self.server.signal(
                "api-websites-site-publish-target",
                site_id=project.id,
                lane=body.get("lane") or body.get("publish_lane") or "remote_prod",
                controller_id=body.get("controller_id"),
            )
            self._send_json({"ok": True, **payload})
        except (WebsiteProjectError, DeploymentControllerError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-websites-site-publish-target-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_websites_sites(self) -> None:
        try:
            repo_root = self.server.debug_root
            sites = [project.to_dict(repo_root) for project in list_website_projects(repo_root)]
            self.server.signal("api-websites-sites", count=len(sites))
            self._send_json({
                "ok": True,
                "root": str((repo_root / "runtime" / "websites").resolve()),
                "sites": sites,
            })
        except Exception as exc:
            self.server.signal("api-websites-sites-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_websites_site_read(self) -> None:
        try:
            query = parse_qs(urlsplit(self.path).query)
            site_id = str(query.get("site_id", [""])[0] or "")
            payload = read_website_project_files(self.server.debug_root, site_id)
            self.server.signal("api-websites-site-read", site_id=site_id)
            self._send_json({"ok": True, **payload})
        except WebsiteProjectError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-websites-site-read-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _website_builder_chat_enabled_plugin(self, body: dict[str, Any], expected_id: str = "website-builder-edit") -> dict[str, Any]:
        plugins = body.get("mount_plugins")
        if not isinstance(plugins, list):
            plugins = []
        for plugin in plugins:
            if isinstance(plugin, dict) and plugin.get("id") == expected_id and plugin.get("enabled") is not False:
                return plugin
        state = body.get("mount_plugin_state")
        if isinstance(state, dict):
            plugin_state = state.get(expected_id)
            if isinstance(plugin_state, dict) and plugin_state.get("enabled") is not False:
                return {"id": expected_id, "enabled": True}
        raise ValueError("Website Builder edit chat requires the checked website-builder-edit mount plugin.")

    def _website_builder_chat_require_mount(self, body: dict[str, Any]) -> None:
        embedded = body.get("embedded_context") if isinstance(body.get("embedded_context"), dict) else {}
        source = body.get("embedded_context_source") if isinstance(body.get("embedded_context_source"), dict) else {}
        active_app = str(embedded.get("active_app") or source.get("active_app") or "").strip()
        if active_app != "website-builder":
            raise ValueError("Website Builder edit chat must come from a Website Builder embedded chat mount.")

    def _website_builder_chat_site_id(self, body: dict[str, Any], plugin: dict[str, Any]) -> str:
        embedded = body.get("embedded_context") if isinstance(body.get("embedded_context"), dict) else {}
        source = body.get("embedded_context_source") if isinstance(body.get("embedded_context_source"), dict) else {}
        for value in (
            embedded.get("site_id"),
            embedded.get("project_id"),
            embedded.get("target_id"),
            source.get("target_id"),
            body.get("site_id"),
            body.get("project_id"),
            body.get("target_id"),
            plugin.get("site_id"),
            plugin.get("project_id"),
            plugin.get("target_id"),
        ):
            text = str(value or "").strip()
            if text:
                return validate_site_id(text)
        projects = list_website_projects(self.server.debug_root)
        if not projects:
            raise WebsiteProjectError("No Website Builder sites are available.")
        return projects[0].id

    def _website_builder_visible_site_files(self, project: Any, *, limit: int = 80) -> list[str]:
        root = project.path.resolve()
        visible: list[str] = []
        for name in ("site.json", "index.html", "style.css", "script.js", "builder.json"):
            path = root / name
            if path.is_file():
                visible.append(f"runtime/websites/{project.id}/{name}")
        for folder in ("assets", "data", "blog", ".main-computer"):
            folder_root = root / folder
            if not folder_root.is_dir():
                continue
            for path in sorted(folder_root.rglob("*")):
                if path.is_file():
                    visible.append(f"runtime/websites/{project.id}/{path.relative_to(root).as_posix()}")
                    if len(visible) >= limit:
                        return visible
        return visible

    def _website_builder_builder_allowlist(self) -> list[str]:
        try:
            return [str(path) for path in discover_website_builder_rag_allowlist(Path(self.server.debug_root))]
        except Exception:
            candidates = [
                "main_computer/web/applications/scripts/website-builder.js",
                "main_computer/web/applications/styles/website-builder.css",
                "main_computer/viewport_routes_applications.py",
                "main_computer/website_project_manifest.py",
                "main_computer/rag_website_builder_real_edit_smoke.py",
                "tests/test_website_builder_app.py",
            ]
            return [path for path in candidates if (Path(self.server.debug_root) / path).is_file()]

    def _website_builder_scoped_chat_context(self, *, site_id: str, project: Any, payload: dict[str, Any], visible_files: list[str]) -> str:
        file_lines = "\n".join(f"- `{path}`" for path in visible_files) or "- No files are present in this website project yet."
        builder_lines = "\n".join(f"- `{path}`" for path in self._website_builder_builder_allowlist()) or "- No Website Builder implementation files were discovered."
        return (
            "You are answering inside the mounted Website Builder chat.\n"
            f"Active site id: `{site_id}`.\n"
            f"Allowed site root: `runtime/websites/{site_id}/`.\n"
            f"Allowed root: `runtime/websites/{site_id}/`.\n"
            "Allowed builder implementation files are exact-match allowlist entries only.\n"
            "Do not claim repo-wide access. Do not trust client-provided paths as authority.\n"
            "For edit requests, the route uses evidence -> AI structured proposal -> deterministic validation -> full replacement payloads.\n"
            "This mounted phase is proposal-only: no files may be modified live.\n\n"
            "Visible website-project files:\n"
            f"{file_lines}\n\n"
            "Visible Website Builder implementation allowlist:\n"
            f"{builder_lines}\n\n"
            f"Site kind: `{project.kind}`; lane: `{project.lane}`; HTML bytes: {len(payload.get('html') or '')}; CSS bytes: {len(payload.get('css') or '')}; JS bytes: {len(payload.get('js') or '')}.\n"
        )

    def _website_builder_scope_response(self, *, source: str, site_id: str, project: Any, payload: dict[str, Any], visible_files: list[str], run_id: str, thread_id: str) -> ChatResponse:
        file_lines = "\n".join(f"- `{path}`" for path in visible_files) or "- No files are present in this website project yet."
        builder_allowlist = self._website_builder_builder_allowlist()
        builder_lines = "\n".join(f"- `{path}`" for path in builder_allowlist) or "- No Website Builder implementation files were discovered."
        content = (
            f"I am scoped to the active Website Builder site `{site_id}` plus an exact Website Builder implementation allowlist.\n\n"
            "Visible website-project files:\n"
            f"{file_lines}\n\n"
            "Visible Website Builder implementation allowlist:\n"
            f"{builder_lines}\n\n"
            "Scope lock:\n"
            f"- Allowed site root: `runtime/websites/{site_id}/`\n"
            "- Builder edits must match one of the listed allowlist paths exactly.\n"
            "- Client-provided paths are treated as hints only; the server derives and validates scope.\n"
            "- Server-derived write policy: proposal-only; no files were modified.\n"
            "- Repo-wide files, tools, historical patch reports, and other sites are outside this mounted editor context.\n\n"
            f"Site kind: `{project.kind}`; lane: `{project.lane}`; HTML bytes: {len(payload.get('html') or '')}; CSS bytes: {len(payload.get('css') or '')}; JS bytes: {len(payload.get('js') or '')}.\n\n"
            "For edit requests, this route returns deterministic validation metadata and materialized replacement payloads for review."
        )
        return ChatResponse(
            content=content,
            provider="main-computer-mounted-editor",
            model="website-builder-scoped-chat",
            metadata={
                "run_id": run_id,
                "thread_id": thread_id,
                "editor_edit_mode": "website-builder",
                "editor_intent": "scope",
                "site_id": site_id,
                "allowed_root": f"runtime/websites/{site_id}/",
                "allowed_roots": [f"runtime/websites/{site_id}/"],
                "builder_allowlist": builder_allowlist,
                "visible_files": visible_files,
                "prompt": source,
                "auto_apply": False,
                "scope_card": True,
            },
        )

    def _website_builder_scoped_ai_response(self, *, cell: dict[str, Any], source: str, site_id: str, visible_files: list[str], run_id: str, thread_id: str, scoped_context: str) -> ChatResponse:
        log_path = self._chat_console_session_log_path(run_id)
        attachments = self._chat_console_evaluation_attachments(cell.get("attachments") if isinstance(cell.get("attachments"), list) else [])
        append_text_log(
            log_path,
            "route accepted mounted Website Builder AI request",
            run_id=run_id,
            thread_id=thread_id,
            site_id=site_id,
            source_chars=len(source),
            source=source,
            visible_files=visible_files,
            scoped_context_chars=len(scoped_context),
        )
        self.server.activity.record(
            source="website-builder",
            kind="ai",
            time_model="parallel",
            severity="info",
            title="Website Builder scoped AI request queued",
            message=source[:500],
            status="running",
            tags=["ai", "local-ai", "chat-console", "website-builder", "mounted-editor", "subprocess"],
            data={
                "run_id": run_id,
                "thread_id": thread_id,
                "activity_filter": "ai",
                "editor_edit_mode": "website-builder",
                "site_id": site_id,
                "allowed_root": f"runtime/websites/{site_id}/",
                "visible_files": visible_files,
                "raw_thinking_exposed": False,
                "running_text": "Website Builder scoped AI subprocess queued",
                "rag_type": "website_builder_scoped_chat",
            },
        )
        if _mounted_editor_should_inline_test_provider(getattr(getattr(self.server, "computer", None), "provider", None)):
            inline_source = f"{scoped_context}\n\nUser request:\n{source}"
            if hasattr(self.server.computer, "chat_console_ai"):
                inline_response = self.server.computer.chat_console_ai(inline_source, attachments=attachments)
            else:
                inline_response = self.server.computer.chat(inline_source)
            payload = {
                "response": {
                    "content": inline_response.content,
                    "provider": inline_response.provider,
                    "model": inline_response.model,
                    "metadata": inline_response.metadata,
                }
            }
        else:
            payload = self.server.chat_ai_processes.run(
                command={
                    "mode": "chat_console_ai",
                    "run_id": run_id,
                    "source": source,
                    "attachments": attachments,
                    "config": config_to_payload(self.server.config),
                    "scoped_context": {
                        "label": "website-builder",
                        "text": scoped_context,
                    },
                },
                thread_id=thread_id,
                log_file=log_path,
                activity_bus=self.server.activity,
                cwd=self.server.debug_root,
            )
        response_payload = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        response = ChatResponse(
            content=str(response_payload.get("content") or ""),
            provider=str(response_payload.get("provider") or getattr(getattr(self.server.computer, "provider", None), "name", "")),
            model=str(response_payload.get("model") or getattr(getattr(self.server.computer, "provider", None), "model", "")),
            metadata=response_payload.get("metadata") if isinstance(response_payload.get("metadata"), dict) else {},
        )
        append_text_log(
            log_path,
            "route completed mounted Website Builder AI request",
            run_id=run_id,
            thread_id=thread_id,
            site_id=site_id,
            response_chars=len(response.content),
            provider=response.provider,
            model=response.model,
        )
        return response

    def _website_builder_parse_jsonish(self, source: str) -> dict[str, Any]:
        text = str(source or "").strip()
        if not text:
            raise ValueError("AI response was empty; expected Website Builder proposal JSON.")
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
        if "```" in text:
            text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text.strip())
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= start:
            payload = json.loads(text[start : end + 1])
            if isinstance(payload, dict):
                return payload
        raise ValueError("AI response did not contain a JSON object proposal.")

    def _website_builder_write_rag_outputs(self, *, site_id: str, evidence: dict[str, Any], proposal: dict[str, Any], materialized: list[Any], validation: dict[str, Any]) -> dict[str, str]:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        output_dir = Path(self.server.debug_root) / "diagnostics_output" / "website_builder_mount_rag_proposals" / f"{site_id}_{stamp}"
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = write_website_builder_rag_bundle(Path(self.server.debug_root), output_dir, materialized)

        def write_json(path: Path, payload: Any) -> None:
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        write_json(output_dir / "evidence.json", evidence)
        write_json(output_dir / "proposal.json", proposal)
        write_json(output_dir / "validation.json", validation)
        return {
            "output_dir": str(output_dir),
            "manifest": str(output_dir / "manifest.json"),
            "reference_patch": str(output_dir / "reference.patch"),
            "payload_root": str(output_dir / "files"),
            "manifest_mode": str(manifest.get("mode") or ""),
        }

    def _website_builder_materialized_file_dicts(self, materialized: list[Any], *, include_text: bool) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in materialized:
            row = {
                "path": str(getattr(item, "path", "")),
                "operation": str(getattr(item, "operation", "")),
                "original_sha256": getattr(item, "original_sha256", None),
                "replacement_sha256": getattr(item, "replacement_sha256", None),
            }
            if include_text:
                row["replacement_text"] = str(getattr(item, "replacement_text", ""))
            rows.append(row)
        return rows

    def _website_builder_rag_auto_apply_requested(self, *, body: dict[str, Any], plugin: dict[str, Any]) -> bool:
        """Return true only when the mounted Website Builder plugin explicitly requests live apply."""
        candidates = [
            body.get("auto_apply"),
            body.get("live_apply"),
            body.get("apply"),
            plugin.get("auto_apply") if isinstance(plugin, dict) else None,
            plugin.get("live_apply") if isinstance(plugin, dict) else None,
        ]
        return any(value is True or str(value).strip().lower() in {"1", "true", "yes", "apply", "live"} for value in candidates)

    def _website_builder_rag_safe_relpath(self, raw: object) -> str:
        text = str(raw or "").replace("\\", "/").strip().lstrip("/")
        parts = [part for part in text.split("/") if part and part != "."]
        if not parts or any(part == ".." for part in parts):
            raise ValueError(f"Unsafe RAG apply path: {raw!r}")
        return "/".join(parts)

    def _website_builder_rag_path_allowed(self, rel_path: str, evidence: dict[str, Any]) -> bool:
        safe = self._website_builder_rag_safe_relpath(rel_path)
        site_root = str(evidence.get("site", {}).get("site_root") or "").rstrip("/")
        builder_allowlist = {str(item) for item in evidence.get("builder_allowlist") or []}
        return bool(
            (site_root and (safe == site_root or safe.startswith(site_root + "/")))
            or safe in builder_allowlist
        )

    def _website_builder_rag_repo_path(self, rel_path: str, evidence: dict[str, Any]) -> Path:
        safe = self._website_builder_rag_safe_relpath(rel_path)
        if not self._website_builder_rag_path_allowed(safe, evidence):
            raise ValueError(f"RAG apply path is not allowed: {safe!r}")
        target = (Path(self.server.debug_root) / safe).resolve()
        root = Path(self.server.debug_root).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"RAG apply path escapes workspace: {safe!r}") from exc
        return target

    def _website_builder_rag_sha256_text(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _website_builder_atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)

    def _website_builder_append_rag_apply_log(self, *, site_id: str, written: list[dict[str, Any]], warnings: list[str]) -> None:
        try:
            log_dir = Path(self.server.debug_root) / "diagnostics_output" / "website_builder_rag_apply"
            log_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "site_id": site_id,
                "files": written,
                "warnings": warnings,
            }
            with (log_dir / "apply_log.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
        except Exception:
            # Logging must not turn an already-validated write into a failed user edit.
            pass

    def _website_builder_rag_apply_payloads(self, *, site_id: str, payloads: list[Any]) -> dict[str, Any]:
        evidence = build_website_builder_rag_evidence(Path(self.server.debug_root), site_id)
        issues: list[str] = []
        warnings: list[str] = []
        write_plan: list[dict[str, Any]] = []
        written: list[dict[str, Any]] = []

        if not isinstance(payloads, list) or not payloads:
            raise ValueError("payloads must be a non-empty list.")

        for index, item in enumerate(payloads):
            if not isinstance(item, dict):
                issues.append(f"payloads[{index}] must be an object")
                continue
            try:
                rel_path = self._website_builder_rag_safe_relpath(item.get("path"))
            except Exception as exc:  # noqa: BLE001 - surfaced as validation feedback.
                issues.append(f"payloads[{index}] {exc}")
                continue

            operation = str(item.get("operation") or "").strip().lower()
            replacement_text = item.get("replacement_text")
            if operation not in {"modify", "create"}:
                issues.append(f"payloads[{index}] operation must be modify or create")
                continue
            if not isinstance(replacement_text, str):
                issues.append(f"payloads[{index}] replacement_text must be a string")
                continue

            try:
                target = self._website_builder_rag_repo_path(rel_path, evidence)
            except Exception as exc:  # noqa: BLE001 - surfaced as validation feedback.
                issues.append(f"payloads[{index}] {exc}")
                continue

            replacement_sha256 = self._website_builder_rag_sha256_text(replacement_text)
            expected_replacement_sha256 = item.get("replacement_sha256")
            if expected_replacement_sha256 and expected_replacement_sha256 != replacement_sha256:
                issues.append(f"payloads[{index}] replacement_sha256 mismatch for {rel_path}")
                continue

            current_exists = target.exists()
            if operation == "modify" and not current_exists:
                issues.append(f"payloads[{index}] cannot modify missing file: {rel_path}")
                continue
            if operation == "create" and current_exists:
                issues.append(f"payloads[{index}] cannot create existing file: {rel_path}")
                continue

            current_sha256 = None
            if current_exists:
                current_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
            expected_original_sha256 = item.get("original_sha256")
            if operation == "modify" and expected_original_sha256 != current_sha256:
                issues.append(
                    f"payloads[{index}] original_sha256 mismatch for {rel_path}: "
                    f"expected {expected_original_sha256!r}, found {current_sha256!r}"
                )
                continue
            if operation == "create" and expected_original_sha256 not in {None, ""}:
                issues.append(f"payloads[{index}] create original_sha256 must be null for {rel_path}")
                continue

            if rel_path.endswith(".json"):
                try:
                    json.loads(replacement_text)
                except json.JSONDecodeError as exc:
                    issues.append(f"payloads[{index}] replacement JSON is invalid for {rel_path}: {exc}")
                    continue

            write_plan.append(
                {
                    "path": rel_path,
                    "target": target,
                    "operation": operation,
                    "original_sha256": current_sha256,
                    "replacement_sha256": replacement_sha256,
                    "replacement_text": replacement_text,
                }
            )

        if issues:
            return {
                "ok": False,
                "site_id": site_id,
                "mode": "rag-validated-live-apply",
                "allowed_root": evidence.get("site", {}).get("site_root"),
                "allowed_roots": evidence.get("allowed_roots"),
                "builder_allowlist": evidence.get("builder_allowlist"),
                "files": [],
                "issues": issues,
                "warnings": warnings,
            }

        for item in write_plan:
            target = item["target"]
            self._website_builder_atomic_write(target, str(item["replacement_text"]).encode("utf-8"))
            written.append(
                {
                    "path": item["path"],
                    "operation": item["operation"],
                    "original_sha256": item["original_sha256"],
                    "replacement_sha256": item["replacement_sha256"],
                    "written_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            )

        self._website_builder_append_rag_apply_log(site_id=site_id, written=written, warnings=warnings)
        self.server.signal(
            "api-website-builder-rag-apply",
            site_id=site_id,
            files=[item["path"] for item in written],
            count=len(written),
        )
        return {
            "ok": True,
            "site_id": site_id,
            "mode": "rag-validated-live-apply",
            "allowed_root": evidence.get("site", {}).get("site_root"),
            "allowed_roots": evidence.get("allowed_roots"),
            "builder_allowlist": evidence.get("builder_allowlist"),
            "files": written,
            "issues": issues,
            "warnings": warnings,
        }

    def _handle_website_builder_rag_apply(self) -> None:
        try:
            body = self._read_json()
            plugin = self._website_builder_chat_enabled_plugin(body)
            self._website_builder_chat_require_mount(body)
            site_id = self._website_builder_chat_site_id(body, plugin)
            payloads = body.get("payloads")
            if payloads is None:
                proposal = body.get("proposal") if isinstance(body.get("proposal"), dict) else {}
                payloads = proposal.get("apply_payloads")
            result = self._website_builder_rag_apply_payloads(site_id=site_id, payloads=payloads if isinstance(payloads, list) else [])
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send_json(result, status=status)
        except WebsiteProjectError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-website-builder-rag-apply-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _website_builder_proposal_response(
        self,
        *,
        body: dict[str, Any],
        plugin: dict[str, Any],
        cell: dict[str, Any],
        source: str,
        site_id: str,
        visible_files: list[str],
        run_id: str,
        thread_id: str,
    ) -> ChatResponse:
        evidence = build_website_builder_rag_evidence(Path(self.server.debug_root), site_id)
        prompt_text = website_builder_rag_proposal_prompt(source, evidence)
        ai_response = self._website_builder_scoped_ai_response(
            cell=cell,
            source=source,
            site_id=site_id,
            visible_files=visible_files,
            run_id=run_id,
            thread_id=thread_id,
            scoped_context=prompt_text,
        )

        requested_auto_apply = self._website_builder_rag_auto_apply_requested(body=body, plugin=plugin)
        warnings = [
            "The mounted Website Builder route used the golden-path RAG shape: scoped evidence, AI structured proposal, deterministic validation, materialized full replacement payloads, and guarded apply when requested.",
        ]
        if requested_auto_apply:
            warnings.append("Auto-apply requested: validated replacements will be written only after deterministic validation passes.")
        else:
            warnings.append("Proposal-only mode: no files were modified.")
        proposal_payload: dict[str, Any] | None = None
        materialized: list[Any] = []
        validation: dict[str, Any] = {"ok": False, "issues": [], "warnings": []}
        outputs: dict[str, str] = {}

        try:
            proposal_payload = self._website_builder_parse_jsonish(ai_response.content)
            materialized, validation = materialize_website_builder_rag_proposal(Path(self.server.debug_root), evidence, proposal_payload)
            if validation.get("ok"):
                outputs = self._website_builder_write_rag_outputs(
                    site_id=site_id,
                    evidence=evidence,
                    proposal=proposal_payload,
                    materialized=materialized,
                    validation=validation,
                )
        except Exception as exc:  # noqa: BLE001 - return deterministic validation feedback to the mounted chat UI.
            validation = {"ok": False, "issues": [str(exc)], "warnings": []}
            materialized = []

        if validation.get("warnings"):
            warnings.extend(str(item) for item in validation.get("warnings", []) if str(item).strip())

        materialized_files = self._website_builder_materialized_file_dicts(materialized, include_text=False)
        apply_payloads = self._website_builder_materialized_file_dicts(materialized, include_text=True)
        proposed_files = [
            {
                "path": item["path"],
                "operation": item["operation"],
                "reason": "Validated and materialized from the AI proposal.",
                "exists": item["operation"] == "modify",
                "original_sha256": item.get("original_sha256"),
                "replacement_sha256": item.get("replacement_sha256"),
            }
            for item in materialized_files
        ]

        if proposed_files:
            file_lines = "\n".join(
                f"- `{item['path']}` ({item['operation']}): replacement `{item.get('replacement_sha256')}`"
                for item in proposed_files
            )
        else:
            file_lines = "- No replacement payloads were materialized."

        proposal = {
            "version": 2,
            "type": "website-builder-rag-proposal",
            "mode": "pending-apply" if requested_auto_apply else "proposal-only",
            "rag_backed": True,
            "ai_backed": True,
            "auto_apply": requested_auto_apply,
            "site_id": site_id,
            "allowed_root": evidence.get("site", {}).get("site_root"),
            "allowed_roots": evidence.get("allowed_roots"),
            "builder_allowlist": evidence.get("builder_allowlist"),
            "prompt": source,
            "validation": validation,
            "proposed_files": proposed_files,
            "materialized_files": materialized_files,
            "apply_payloads": apply_payloads,
            "outputs": outputs,
            "model_proposal": proposal_payload,
            "warnings": warnings,
            "evidence_summary": {
                "site_files": len(evidence.get("site", {}).get("site_files", [])),
                "editable_site_records": len(evidence.get("site", {}).get("editable_site_records", [])),
                "builder_source_files": len(evidence.get("builder", {}).get("builder_files", [])),
            },
        }

        apply_result: dict[str, Any] | None = None
        if requested_auto_apply and validation.get("ok") and apply_payloads:
            apply_result = self._website_builder_rag_apply_payloads(site_id=site_id, payloads=apply_payloads)
            proposal["apply_result"] = apply_result
            proposal["mode"] = "applied" if apply_result.get("ok") else "apply-failed"
            warnings.append(
                "Applied validated Website Builder RAG replacement payloads to the workspace."
                if apply_result.get("ok")
                else "Auto-apply was requested, but the guarded apply step failed; source files were not fully updated."
            )
        elif requested_auto_apply and not validation.get("ok"):
            warnings.append("Auto-apply was requested, but deterministic validation failed; no files were modified.")
        elif requested_auto_apply and not apply_payloads:
            warnings.append("Auto-apply was requested, but no replacement payloads were materialized; no files were modified.")

        validation_text = "passed" if validation.get("ok") else "failed"
        issue_lines = "\n".join(f"- {issue}" for issue in validation.get("issues", []) if str(issue).strip())
        output_lines = ""
        if outputs:
            output_lines = (
                "\nReview artifacts:\n"
                f"- Manifest: `{outputs.get('manifest')}`\n"
                f"- Reference patch: `{outputs.get('reference_patch')}`\n"
            )

        if apply_result:
            apply_text = "applied" if apply_result.get("ok") else "apply failed"
            apply_lines = "\n".join(
                f"- `{item.get('path')}` ({item.get('operation')}): wrote `{item.get('written_sha256')}`"
                for item in apply_result.get("files", [])
                if isinstance(item, dict)
            ) or "- No files were written."
            heading = (
                "Applied — Website Builder golden-path RAG wrote validated replacement files.\n\n"
                if apply_result.get("ok")
                else "Apply failed — Website Builder golden-path RAG did not complete the write.\n\n"
            )
            apply_section = f"Apply result: **{apply_text}**.\n{apply_lines}\n\n"
        else:
            heading = "Proposal only — Website Builder golden-path RAG; no files were modified.\n\n"
            apply_section = ""

        content = (
            heading
            + f"Deterministic validation: **{validation_text}**.\n\n"
            + apply_section
            + "Validated/materialized file targets for review:\n"
            + f"{file_lines}\n"
            + f"{output_lines}\n"
            + "Review notes:\n"
            + "\n".join(f"- {warning}" for warning in warnings)
        )
        if issue_lines:
            content += f"\n\nValidation issues:\n{issue_lines}"
        ai_content = str(ai_response.content or "").strip()
        if ai_content:
            content += f"\n\nAI structured proposal/raw response:\n```json\n{ai_content}\n```"

        response_metadata = ai_response.metadata if isinstance(ai_response.metadata, dict) else {}
        return ChatResponse(
            content=content,
            provider=ai_response.provider,
            model=ai_response.model,
            metadata={
                **response_metadata,
                "run_id": run_id,
                "thread_id": thread_id,
                "editor_edit_mode": "website-builder",
                "editor_intent": "apply_edit" if apply_result and apply_result.get("ok") else "propose_edit",
                "site_id": site_id,
                "allowed_root": evidence.get("site", {}).get("site_root"),
                "allowed_roots": evidence.get("allowed_roots"),
                "builder_allowlist": evidence.get("builder_allowlist"),
                "visible_files": visible_files,
                "prompt": source,
                "auto_apply": requested_auto_apply,
                "apply_result": apply_result,
                "scope_card": False,
                "proposal": proposal,
            },
        )

    def _handle_website_builder_chat_edit(self) -> None:
        try:
            body = self._read_json()
            cell = body.get("cell") if isinstance(body.get("cell"), dict) else {}
            cell_type, source = validate_evaluation_cell(cell)
            if cell_type != "ai":
                raise ValueError("Website Builder edit chat only accepts AI cells.")
            plugin = self._website_builder_chat_enabled_plugin(body)
            self._website_builder_chat_require_mount(body)
            site_id = self._website_builder_chat_site_id(body, plugin)
            project = load_website_project(self.server.debug_root, site_id)
            payload = read_website_project_files(self.server.debug_root, site_id)
            visible_files = self._website_builder_visible_site_files(project)
            run_id = str(body.get("run_id") or cell.get("run_id") or f"website_builder_edit_{int(time.time() * 1000)}").strip()
            thread_id = str(body.get("thread_id") or body.get("chat_thread_id") or "website-builder-chat").strip()
            scoped_context = self._website_builder_scoped_chat_context(site_id=site_id, project=project, payload=payload, visible_files=visible_files)
            scope_card = _mounted_editor_scope_query(source)
            proposal_request = _mounted_editor_edit_request(source)
            if scope_card:
                response = self._website_builder_scope_response(
                    source=source,
                    site_id=site_id,
                    project=project,
                    payload=payload,
                    visible_files=visible_files,
                    run_id=run_id,
                    thread_id=thread_id,
                )
            elif proposal_request:
                response = self._website_builder_proposal_response(
                    body=body,
                    plugin=plugin,
                    cell=cell,
                    source=source,
                    site_id=site_id,
                    visible_files=visible_files,
                    run_id=run_id,
                    thread_id=thread_id,
                )
            else:
                response = self._website_builder_scoped_ai_response(
                    cell=cell,
                    source=source,
                    site_id=site_id,
                    visible_files=visible_files,
                    run_id=run_id,
                    thread_id=thread_id,
                    scoped_context=scoped_context,
                )
            output_cell = build_output_cell(cell, ai_response_to_parts(response), status="ok", provider=response.provider, model=response.model)
            output_cell.setdefault("metadata", {})
            response_metadata = response.metadata if isinstance(response.metadata, dict) else {}
            editor_intent = str(response_metadata.get("editor_intent") or ("scope" if scope_card else "propose_edit" if proposal_request else "answer"))
            output_cell["metadata"] = {
                **(output_cell.get("metadata") if isinstance(output_cell.get("metadata"), dict) else {}),
                **response_metadata,
                "run_id": run_id,
                "thread_id": thread_id,
                "activity_filter": "ai",
                "editor_edit_mode": "website-builder",
                "editor_intent": editor_intent,
                "site_id": site_id,
                "allowed_root": response_metadata.get("allowed_root") or f"runtime/websites/{site_id}/",
                "allowed_roots": response_metadata.get("allowed_roots") if isinstance(response_metadata.get("allowed_roots"), list) else [f"runtime/websites/{site_id}/"],
                "builder_allowlist": response_metadata.get("builder_allowlist") if isinstance(response_metadata.get("builder_allowlist"), list) else self._website_builder_builder_allowlist(),
                "visible_files": visible_files,
                "auto_apply": bool(response_metadata.get("auto_apply")),
                "apply_result": response_metadata.get("apply_result"),
                "scope_card": scope_card,
            }
            self.server.chat_ai_processes.remember_route_result(run_id=run_id, payload={"ok": True, "status": "completed", "output_cell": output_cell, "run_id": run_id, "thread_id": thread_id})
            self.server.signal("api-website-builder-chat-edit", site_id=site_id, prompt_chars=len(source), visible_files=len(visible_files), scope_card=scope_card, proposal_request=proposal_request)
            self._send_json({"ok": True, "status": "completed", "output_cell": output_cell, "run_id": run_id, "thread_id": thread_id})
        except WebsiteProjectError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-website-builder-chat-edit-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _handle_websites_site_create(self) -> None:
        try:
            body = self._read_json()
            project, local_platform_result = create_local_platform_website_project(
                self.server.debug_root,
                body.get("site_id") or body.get("id"),
                body.get("name"),
                kind=body.get("kind") or "static-site",
                allocate_unique_id=True,
            )
            self.server.signal("api-websites-site-create", site_id=project.id)
            self._send_json(
                {
                    "ok": True,
                    "site": project.to_dict(self.server.debug_root),
                    "local_platform_registration": local_platform_result["registry"],
                    "generated_compose": local_platform_result["compose"],
                }
            )
        except WebsiteProjectError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-websites-site-create-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_websites_site_archive(self) -> None:
        try:
            body = self._read_json()
            result = archive_website_project(
                self.server.debug_root,
                body.get("site_id") or body.get("id"),
            )
            self.server.signal("api-websites-site-archive", site_id=result["site_id"])
            self._send_json({"ok": True, "archive": result})
        except WebsiteProjectError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-websites-site-archive-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_websites_site_save(self) -> None:
        try:
            body = self._read_json()
            project = save_website_project_files(
                self.server.debug_root,
                body.get("site_id") or body.get("id"),
                html=body.get("html") if "html" in body else None,
                css=body.get("css") if "css" in body else None,
                js=body.get("js") if "js" in body else None,
                builder=body.get("builder") if "builder" in body else None,
            )
            payload = read_website_project_files(self.server.debug_root, project.id)
            self.server.signal("api-websites-site-save", site_id=project.id)
            self._send_json({"ok": True, **payload})
        except WebsiteProjectError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-websites-site-save-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_websites_site_publish(self) -> None:
        try:
            body = self._read_json()
            site_id = body.get("site_id") or body.get("id")
            saved_project = None
            if any(key in body for key in ("html", "css", "js", "builder")):
                saved_project = save_website_project_files(
                    self.server.debug_root,
                    site_id,
                    html=body.get("html") if "html" in body else None,
                    css=body.get("css") if "css" in body else None,
                    js=body.get("js") if "js" in body else None,
                    builder=body.get("builder") if "builder" in body else None,
                )
            requested_lane = body.get("lane") or body.get("publish_lane") or "remote_prod"
            directus_connection_project = None
            if str(requested_lane or "").strip().lower().replace("_", "-") == "local" and body.get("directus_connection"):
                directus_connection_project = save_website_directus_connection(
                    self.server.debug_root,
                    site_id,
                    body.get("directus_connection"),
                )
            result = publish_website(
                self.server.debug_root,
                site_id,
                lane=requested_lane,
                dry_run=bool(body.get("dry_run")),
                verify=not bool(body.get("no_verify")),
            )
            plan = result.get("plan", {}) if isinstance(result, dict) else {}
            self.server.signal(
                "api-websites-site-publish",
                site_id=site_id,
                lane=requested_lane,
                service=plan.get("service") if isinstance(plan, dict) else "",
                saved_before_publish=bool(saved_project),
            )
            payload = {"ok": bool(result.get("ok")), "result": result}
            if directus_connection_project is not None:
                payload["directus_connection"] = directus_connection_project.to_dict(self.server.debug_root).get("backend", {}).get("cms", {}).get("local_connection", {})
            if not payload["ok"]:
                payload["error"] = _website_publish_error_message(result)
            result_site = result.get("site") if isinstance(result, dict) else None
            if isinstance(result_site, dict):
                payload["site"] = result_site
            elif saved_project is not None:
                payload["site"] = saved_project.to_dict(self.server.debug_root)
            self._send_json(payload)
        except WebsiteProjectError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-websites-site-publish-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_blog_install_assumptions(self) -> None:
        try:
            parts = [part for part in urlsplit(self.path).path.split("/") if part]
            site_id = parts[2] if len(parts) >= 5 else ""
            result = blog_install_assumptions(self.server.debug_root, site_id)
            self.server.signal("api-blog-install-assumptions", site_id=result.get("site_id"))
            self._send_json(result)
        except (WebsiteProjectError, BlogInstallError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-blog-install-assumptions-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_blog_intent(self) -> None:
        try:
            parts = [part for part in urlsplit(self.path).path.split("/") if part]
            site_id = parts[2] if len(parts) >= 5 else ""
            body = self._read_json()
            result = persist_blog_intent(self.server.debug_root, site_id, body)
            self.server.signal(
                "api-blog-intent",
                site_id=site_id,
                ok=bool(result.get("ok")),
                install_status=(result.get("intent") or {}).get("install_status", ""),
            )
            self._send_json(result)
        except (WebsiteProjectError, BlogInstallError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-blog-intent-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


    def _handle_blog_layer_install(self) -> None:
        try:
            parts = [part for part in urlsplit(self.path).path.split("/") if part]
            site_id = parts[2] if len(parts) >= 7 else ""
            layer_id = parts[5] if len(parts) >= 7 else ""
            body = self._read_json()
            result = install_blog_layer(self.server.debug_root, site_id, layer_id, body)
            self.server.signal(
                "api-blog-layer-install",
                site_id=site_id,
                layer_id=layer_id,
                ok=bool(result.get("ok")),
                action=result.get("action", ""),
            )
            self._send_json(result)
        except (WebsiteProjectError, BlogInstallError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.server.signal("api-blog-layer-install-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_spreadsheet_smoke_page(self) -> None:
        try:
            path = Path(__file__).resolve().parent / "web" / "applications" / "apps" / "spreadsheet-smoke.html"
            data = path.read_text(encoding="utf-8")
            self.server.signal("route-spreadsheet-smoke", bytes=len(data))
            self._send_text(data, "text/html; charset=utf-8")
        except Exception as exc:
            self.server.signal("route-spreadsheet-smoke-error", error=exc)
            self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_applications_vendor_asset(self) -> None:
        try:
            route_path = urlsplit(self.path).path
            raw = route_path.removeprefix("/applications/vendor/").replace("\\", "/")
            parts = [part for part in raw.split("/") if part and part != "."]
            if not parts or any(part == ".." for part in parts):
                raise ValueError("Application vendor asset path is invalid.")
            root = (Path(__file__).resolve().parent / "web" / "applications" / "vendor").resolve()
            path = (root.joinpath(*parts)).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError("Application vendor asset path must stay inside applications/vendor.") from exc
            if not path.is_file():
                raise FileNotFoundError("Application vendor asset does not exist.")
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix.lower() == ".js":
                content_type = "text/javascript; charset=utf-8"
            elif path.suffix.lower() == ".map":
                content_type = "application/json; charset=utf-8"
            elif path.suffix.lower() == ".css":
                content_type = "text/css; charset=utf-8"
            data = path.read_bytes()
            self.server.signal("api-applications-vendor-asset", path=path.relative_to(root).as_posix(), bytes=len(data))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            self.server.signal("api-applications-vendor-error", error=exc)
            self.send_error(HTTPStatus.NOT_FOUND)

    def _parse_ollama_ps_output(self, output: str) -> list[dict[str, str]]:
        """Parse legacy ``ollama ps`` fixed-width output into display-safe model rows."""
        lines = [line.rstrip() for line in str(output or "").splitlines() if line.strip()]
        if len(lines) < 2:
            return []

        header = lines[0]
        column_names = ["NAME", "ID", "SIZE", "PROCESSOR", "CONTEXT", "UNTIL"]
        starts: list[tuple[str, int]] = []
        for name in column_names:
            index = header.find(name)
            if index >= 0:
                starts.append((name.lower(), index))
        if len(starts) != len(column_names):
            return []

        rows: list[dict[str, str]] = []
        for line in lines[1:]:
            row: dict[str, str] = {}
            for idx, (name, start) in enumerate(starts):
                end = starts[idx + 1][1] if idx + 1 < len(starts) else None
                row[name] = line[start:end].strip() if end is not None else line[start:].strip()
            if row.get("name"):
                rows.append(row)
        return rows

    def _format_ollama_size(self, value: object) -> str:
        try:
            size = float(value)
        except (TypeError, ValueError):
            return str(value or "")
        units = ("B", "KB", "MB", "GB", "TB")
        index = 0
        while size >= 1024 and index < len(units) - 1:
            size /= 1024
            index += 1
        if index == 0:
            return f"{int(size)} {units[index]}"
        return f"{size:.1f} {units[index]}"

    def _ollama_processor_label(self, model: dict[str, Any]) -> str:
        size = model.get("size")
        size_vram = model.get("size_vram")
        try:
            total = float(size or 0)
            vram = float(size_vram or 0)
        except (TypeError, ValueError):
            return ""
        if total <= 0:
            return ""
        if vram <= 0:
            return "CPU"
        percent = max(0, min(100, round((vram / total) * 100)))
        return f"{percent}% GPU" if percent else "CPU"

    def _ollama_api_model_rows(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        models = payload.get("models")
        if not isinstance(models, list):
            return rows
        for item in models:
            if not isinstance(item, dict):
                continue
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            digest = str(item.get("digest") or item.get("id") or "")
            row = {
                "name": str(item.get("name") or item.get("model") or ""),
                "id": digest[:12],
                "size": self._format_ollama_size(item.get("size")),
                "processor": self._ollama_processor_label(item),
                "context": str(item.get("context") or item.get("context_length") or details.get("parameter_size") or ""),
                "until": str(item.get("expires_at") or item.get("until") or ""),
            }
            if row["name"]:
                rows.append(row)
        return rows

    def _cached_ollama_ps_payload(self) -> dict[str, Any]:
        now = time.monotonic()
        cache = getattr(self.server, "ollama_ps_cache", None)
        if isinstance(cache, dict):
            payload = cache.get("payload")
            expires_at = float(cache.get("expires_at") or 0.0)
            if isinstance(payload, dict) and now < expires_at:
                cached_payload = dict(payload)
                cached_payload["cached"] = True
                return cached_payload

        payload = self._query_ollama_ps_api()
        if isinstance(cache, dict):
            cache["payload"] = dict(payload)
            cache["expires_at"] = now + 20.0
        return payload

    def _query_ollama_ps_api(self) -> dict[str, Any]:
        """Return loaded Ollama models via the configured Ollama HTTP server."""
        base_url = str(getattr(self.server.config, "ollama_base_url", "") or "").rstrip("/")
        if not base_url:
            return {
                "ok": False,
                "models": [],
                "raw_output": "",
                "stderr": "OLLAMA_BASE_URL is not configured",
                "returncode": None,
                "source": "ollama-api",
                "cached": False,
            }

        url = f"{base_url}/api/ps"
        request = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                raw_output = response.read().decode("utf-8", errors="replace")
            payload = json.loads(raw_output or "{}")
            models = self._ollama_api_model_rows(payload if isinstance(payload, dict) else {})
            self.server.signal(
                "api-activity-ollama-ps",
                source="ollama-api",
                model_count=len(models),
                url=url,
            )
            return {
                "ok": True,
                "models": models,
                "raw_output": raw_output,
                "stderr": "",
                "returncode": 0,
                "source": "ollama-api",
                "url": url,
                "cached": False,
            }
        except HTTPError as exc:
            message = f"Ollama API HTTP {exc.code}: {exc.reason}"
        except URLError as exc:
            message = f"Ollama API unavailable: {exc.reason}"
        except TimeoutError:
            message = "Ollama API request timed out"
        except json.JSONDecodeError as exc:
            message = f"Ollama API returned invalid JSON: {exc}"
        except Exception as exc:
            message = f"Ollama API request failed: {exc}"

        self.server.signal("api-activity-ollama-ps", source="ollama-api", model_count=0, error=message, url=url)
        return {
            "ok": False,
            "models": [],
            "raw_output": "",
            "stderr": message,
            "returncode": None,
            "source": "ollama-api",
            "url": url,
            "cached": False,
        }

    def _handle_activity_ollama_ps(self) -> None:
        """Return loaded Ollama model context through the configured Ollama API."""
        self._send_json(self._cached_ollama_ps_payload())

    def _handle_chat(self) -> None:
        try:
            body = self._read_json()
            prompt = str(body.get("prompt", "")).strip()
            if not prompt:
                self.server.signal("api-chat-rejected", reason="empty-prompt")
                self._send_json({"error": "Prompt is required."}, status=HTTPStatus.BAD_REQUEST)
                return
            self.server.signal(
                "api-chat-start",
                provider=self.server.provider_name,
                model=self.server.config.model,
                prompt_chars=len(prompt),
            )
            context_pack = None
            if hasattr(self.server.computer, "context_pack"):
                context_pack = self.server.computer.context_pack(prompt)
                self.server.signal(
                    "api-chat-context-selected",
                    evidence_count=len(context_pack.evidence),
                    manifest_chars=context_pack.manifest_chars,
                    paths="|".join(self._context_evidence_paths(context_pack)),
                    files="|".join(self._context_evidence_paths(context_pack, kind="file")),
                )
            if context_pack is not None:
                response = self.server.computer.chat(prompt, context_pack=context_pack)
            else:
                response = self.server.computer.chat(prompt)
            self.server.signal(
                "api-chat-complete",
                provider=response.provider,
                model=response.model,
                response_chars=len(response.content),
            )
            self._send_json(asdict(response))
        except Exception as exc:
            self.server.signal("api-chat-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)

    def _handle_diagnostics(self) -> None:
        try:
            from main_computer.diagnostics import LEVELS, DiagnosticRunner

            body = self._read_json()
            level = str(body.get("level", "health")).strip()
            if level not in LEVELS:
                self.server.signal("api-diagnostics-rejected", reason="unknown-level", level=level)
                self._send_json({"error": f"Unknown diagnostic level: {level}"}, status=HTTPStatus.BAD_REQUEST)
                return

            output_dir = Path.cwd() / "diagnostics_output_viewport" / level

            self.server.signal("api-diagnostics-start", level=level)
            report = DiagnosticRunner(
                config=self.server.config,
                level=level,
                output_dir=output_dir,
                headed=False,
            ).run(raise_on_failure=False)
            self.server.signal(
                "api-diagnostics-complete",
                level=level,
                checks=len(report.get("checks", [])),
                ok=report.get("ok"),
            )
            if not report.get("ok"):
                self.server.signal(
                    "api-diagnostics-failed-checks",
                    level=level,
                    checks=len(report.get("checks", [])),
                )
            self._send_json(report)
        except Exception as exc:
            self.server.signal("api-diagnostics-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_GATEWAY)

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

    def _workspace_timestamp(self) -> dict[str, Any]:
        monitored_root = self.server.debug_root
        newest_path = monitored_root
        newest_mtime = 0.0
        relevant_files = _iter_workspace_timestamp_files(monitored_root)
        for candidate in relevant_files:
            try:
                candidate_mtime = candidate.stat().st_mtime
            except OSError:
                continue
            if candidate_mtime > newest_mtime:
                newest_mtime = candidate_mtime
                newest_path = candidate
        if newest_mtime <= 0.0 and monitored_root.exists():
            newest_mtime = monitored_root.stat().st_mtime

        latest = datetime.fromtimestamp(newest_mtime, tz=timezone.utc)
        return {
            "workspace": str(monitored_root),
            "patch_level": self.server.config.patch_level,
            "latest_path": str(newest_path),
            "latest_mtime": newest_mtime,
            "latest_mtime_ms": int(newest_mtime * 1000),
            "latest_mtime_iso": latest.isoformat(),
            "poll_interval_ms": 4000,
        }

    def _log_excerpt(self, value: str, limit: int = 4000) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n[truncated after {limit} characters]"

    def _write_aider_debug_artifact(self, event: str, **fields: Any) -> str | None:
        try:
            timestamp = datetime.now(tz=timezone.utc).isoformat()
            record = {
                "timestamp": timestamp,
                "event": event,
                "source": "applications.code_editor.aider",
                **fields,
            }
            content = json.dumps(record, ensure_ascii=False, indent=2, default=str)
            stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            safe_event = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in event.lower())
            safe_event = safe_event.strip("_-") or "action"
            name = self._unique_debug_asset_name(f"aider-{safe_event}-{stamp}.txt")
            path = self._debug_asset_path(name, must_exist=False)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content + "\n", encoding="utf-8")
            self._write_debug_asset_manifest(path.name, f"aider-{safe_event}")
            self.server.signal("aider-debug-artifact", event=event, asset=path.name)
            return path.name
        except Exception as exc:
            self.server.signal("aider-debug-artifact-error", event=event, error=exc)
            return None
