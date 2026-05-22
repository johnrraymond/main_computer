from __future__ import annotations

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
    publish_website,
    read_website_project_files,
    save_website_directus_connection,
    save_website_project_files,
    save_website_publish_target,
)

from main_computer.viewport_state import *  # noqa: F401,F403


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
