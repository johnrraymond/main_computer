from __future__ import annotations

import hashlib
import json
import py_compile
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def test_website_builder_app_is_registered_in_applications_shell() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    navigation = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "navigation.js").read_text(encoding="utf-8")
    routing = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "app-routing.js").read_text(encoding="utf-8")
    dom_bindings = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings.js").read_text(encoding="utf-8")
    route_state = (ROOT / "main_computer" / "viewport_state.py").read_text(encoding="utf-8")

    assert 'data-app="website-builder"' in html
    assert "<!-- @include applications/apps/website-builder.html -->" in html
    assert "<!-- @include applications/styles/website-builder.css -->" in html
    assert "<!-- @include applications/scripts/website-builder.js -->" in html
    assert "<!-- @include applications/scripts/dom-bindings/websites.js -->" in dom_bindings
    assert '"website-builder"' in navigation
    assert "initWebsiteBuilderApp()" in routing
    assert '"website-builder"' in route_state



def test_website_builder_jsonish_parser_accepts_first_object_and_wrapped_json() -> None:
    from main_computer.viewport_routes_applications import ViewportApplicationRoutesMixin

    parser_owner = ViewportApplicationRoutesMixin()

    first = parser_owner._website_builder_parse_jsonish(
        '{"ok": true, "mode": "website_builder_rag_route_decision", "intent": "answer"}\n'
        '{"ok": true, "mode": "ignored_second_object"}'
    )
    assert first["intent"] == "answer"

    wrapped = parser_owner._website_builder_parse_jsonish(
        json.dumps('{"ok": true, "mode": "website_builder_rag_route_decision", "intent": "propose_edit"}')
    )
    assert wrapped["intent"] == "propose_edit"

    fenced = parser_owner._website_builder_parse_jsonish(
        '```json\n{"ok": true, "mode": "website_builder_rag_route_decision", "intent": "scope"}\n```'
    )
    assert fenced["intent"] == "scope"

def test_website_builder_frontend_assets_define_save_and_publish_controls() -> None:
    app = (ROOT / "main_computer" / "web" / "applications" / "apps" / "website-builder.html").read_text(encoding="utf-8")
    script = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "website-builder.js").read_text(encoding="utf-8")
    bindings = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "websites.js").read_text(encoding="utf-8")

    assert "website-builder-save" in app
    assert "website-builder-publish-local" in app
    assert "website-builder-publish-dev" in app
    assert "website-builder-visit-local" in app
    assert "website-builder-visit-dev" in app
    assert "website-builder-visit-remote-prod" in app
    assert "website-builder-visit-local-card" in app
    assert "website-builder-visit-dev-card" in app
    assert "website-builder-visit-remote-prod-card" in app
    assert "website-builder-preview-frame" in app
    assert "website-builder-chat-toggle" in app
    assert "website-builder-chat-panel" in app
    assert "data-chat-console-embed=\"website-builder\"" in app
    assert "website-builder-preview-draft" in app
    assert "website-builder-preview-local" in app
    assert "website-builder-preview-dev" in app
    assert "website-builder-page-runtime-status" in app
    assert 'data-website-builder-page-runtime="default"' in app
    assert "Use MCEL Runtime" in app
    assert "runtime.js" in script
    assert "ensureWebsiteBuilderRuntimeScript" in script
    assert "websiteBuilderPageRuntimeButtons" in bindings
    assert "websiteBuilderPageRuntimeStatus" in bindings
    assert "website-builder-generated-editor-live-apply" in script
    assert "auto_apply: true" in script
    assert "live_apply: true" in script
    assert "refreshWebsiteBuilderAfterRagApply" in script
    assert "main-computer-chat-console-output-applied" in script
    assert "Select a website to use chat." in script
    assert "Website Builder chat requires an active site id." in script
    assert "Deploy" in app
    assert "Local Server" in app
    assert "Publishing" in app
    assert "website-builder-publish-remote" in app
    assert "website-builder-publish-remote-url" in app
    assert "website-builder-prepare-local-publish" not in app
    assert "Prepare to Publish to Local Server" not in app
    assert "website-builder-local-prepare-status" not in app

    assert "website-builder-directus-connection" in app
    assert "website-builder-directus-database-volume" in app
    assert "website-builder-directus-uploads-volume" in app
    assert "website-builder-directus-public-port" in app
    assert "website-builder-directus-connection-ack" in app
    assert "I reviewed this Directus storage binding" in app
    assert "Use existing Directus data" in app
    assert "Create separate empty volumes" in app
    assert "Overwrite old Directus data" in app

    assert "Use Local Server" in app
    assert "Publish slug / remote directory" in app
    assert "Source website folder" in app
    assert "Remote SSH host" in app
    assert "SSH password" in app
    assert "Remote root" in app
    assert "Remote Coolify Python runtime service" in app
    assert "johnrraymond-site" in app
    assert "image: 'python:3.12-slim'" in app
    assert "/app/sites/johnrraymond/.main-computer/runtime/app.py" in app
    assert "DIRECTUS_URL: 'https://directus-johnrraymond.greatlibrary.io'" in app
    assert "/srv/main-computer/sites/johnrraymond:/app/sites/johnrraymond:ro" in app
    assert "127.0.0.1:8080/api/site/status" in app
    assert "Published host / domain" in app
    assert "Blog / Directus" in app
    assert "Published Site Directus URL" in app
    assert "website-builder-publish-blog-directus-card" in app
    assert "website-builder-publish-directus-url" in app
    assert "Command details" in app
    assert 'id="website-builder-publishing-use-local-server" type="checkbox"' in app
    assert 'id="website-builder-publishing-site-slug" type="text" placeholder="johnrraymond"' in app
    assert 'id="website-builder-publishing-source-path" type="text" placeholder="runtime/websites/hub-site"' in app
    assert 'id="website-builder-publishing-ssh-host" type="text" placeholder="root@publish.greatlibrary.io"' in app
    assert 'id="website-builder-publishing-ssh-password" type="password"' in app
    assert 'id="website-builder-publishing-remote-root" type="text" placeholder="/srv/main-computer/sites"' in app
    assert 'id="website-builder-save-remote-prod-target" disabled' in app

    assert "Publishing setup loaded" not in script
    assert "acceptedPublishingSetupSignature" in script
    assert "websiteBuilderVisibleSetupFromSavedPublishTarget" in script
    assert "websiteBuilderPublishingRequiresDirectus" in script
    assert "websiteBuilderPublishDirectusUrlFromSite" in script
    assert "publish_directus_url" in script
    assert 'url.hostname === "0.0.0.0"' in script
    assert 'url.hostname = "localhost"' in script
    assert "websiteBuilderSavedPublishTargetAccepted" in script
    assert "Publishing command setup accepted for" in script
    assert "Re-save this publishing command setup." in script
    assert "accepted_at" in script
    assert "function setWebsiteBuilderPublishingVisibleSetup" in script
    assert "setWebsiteBuilderPublishingVisibleSetup(setupPayload.visible_setup);" in script
    assert "markWebsiteBuilderPublishingSetupAccepted(setupPayload.visible_setup);" in script
    assert "fetch(\"/api/publishing/local-server/prepare\"" not in script
    assert "function websiteBuilderDirectusConnectionForLocalPublish" in script
    assert "function updateWebsiteBuilderDirectusConnectionActions" in script
    assert "Review and confirm the Directus storage binding before publishing locally." in script
    assert "websiteBuilderDirectusConnectionExisting.disabled = !acknowledged" in script
    assert "websiteBuilderDirectusConnectionOverwrite.disabled = !acknowledged" in script
    assert "submitWebsiteBuilderDirectusOverwrite" in script
    assert "websiteBuilderDirectusConnectionForBlogConfigure" in script
    assert "websiteBuilderDirectusConnectionAck?.addEventListener" in script
    assert "payload.directus_connection = options.directusConnection" in script
    assert "websiteBuilderSaveRemoteProdTarget.disabled = websiteBuilderStateModel.busy || !payload.ready_to_accept" in script
    assert "websiteBuilderSaveRemoteProdTarget.disabled = websiteBuilderStateModel.busy || !payload.ready_to_accept || payload.already_accepted" not in script
    assert "body: JSON.stringify(setupPayload.compatibility_payload)" in script
    assert "Phase 1 does not save this setup yet" not in script
    publish_panel = app.split('data-website-builder-panel="publish"', 1)[1].split('data-website-builder-panel="settings"', 1)[0]
    publishing_setup_card = publish_panel.split('class="website-builder-publish-target-card"', 1)[1].split("</article>", 1)[0]
    assert "Coolify target" not in publishing_setup_card
    assert "Environment" not in publishing_setup_card
    assert "Destination" not in publishing_setup_card
    assert "Local Server URL" in app
    assert "Deploy URL" in app
    assert "buildWebsiteBuilderDraftDocument" in script
    assert "setWebsiteBuilderDraftPreview" in script
    assert "mountWebsiteBuilderChat" in script
    assert "website-builder-edit" in script
    assert "/api/applications/website-builder/chat" in script
    assert "setWebsiteBuilderPublishedPreview" in script
    assert "/api/applications/websites/site/save" in script
    assert "/api/applications/websites/site/create" in script
    assert "/api/applications/websites/site/archive" in script
    assert "archiveWebsiteBuilderSite" in script
    assert "Hub Site is protected and cannot be archived." in script
    assert "runtime/websites-archive" in script
    assert "websiteBuilderLaneLabel" in script
    assert "updateWebsiteBuilderVisitButtons" in script
    assert "visitWebsiteBuilderTarget" in script
    assert "normalizeWebsiteBuilderVisitUrl" in script
    assert "websiteBuilderAcceptedPublishTarget" in script
    assert "websiteBuilderRawPublishTarget" in script
    assert "websiteBuilderCanPublishAcceptedSetup" in script
    assert "return Boolean(site?.id && websiteBuilderAcceptedPublishTarget(site));" in script
    assert "refreshWebsiteBuilderSiteFromBackend(siteId)" in script
    assert "websiteBuilderPublishResultUrl" in script
    assert "publishedRemoteProdUrls" in script
    assert "updateWebsiteBuilderPublishActionControls" in script
    assert 'publishWebsiteBuilderSite("remote_prod")' in script
    assert "Accept a publishing setup before publishing." in script
    assert "Published URL:" in script
    assert "controller?.base_url" not in script
    assert "No accepted publishing setup." not in script
    assert "Local Server" in script
    assert "Deploy" in script
    assert "currentWebsiteBuilderFilePayload" in script
    assert "setWebsiteBuilderBusy" in script



def test_website_builder_layout_uses_two_column_workspace_with_rail_manager() -> None:
    app = (ROOT / "main_computer" / "web" / "applications" / "apps" / "website-builder.html").read_text(encoding="utf-8")
    css = (ROOT / "main_computer" / "web" / "applications" / "styles" / "website-builder.css").read_text(encoding="utf-8")
    script = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "website-builder.js").read_text(encoding="utf-8")
    bindings = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "websites.js").read_text(encoding="utf-8")

    assert "website-builder-rail" in app
    assert "website-builder-sidebar" not in app
    assert "website-builder-site-select" in app
    assert "website-builder-create-toggle" in app
    assert "website-builder-archive" in app
    assert "website-builder-git-toggle" in app
    assert "website-builder-git-panel" in app
    assert "website-builder-git-refresh" in app
    assert "website-builder-git-edits" in app
    assert "website-builder-git-accept-head" in app
    assert "website-builder-git-restore-selected" in app
    assert "website-builder-git-revert-selected" in app
    assert "Refresh history" in app
    assert "Restore to inspect" in app
    assert "Make current site HEAD" in app
    assert "Revert patch from HEAD" in app
    assert "Back up" not in app
    assert 'data-website-builder-tab="design"' in app
    assert 'data-website-builder-panel="source"' in app
    assert "grid-template-columns: minmax(720px, 1fr) 320px" in css
    assert ".website-site-card span" in css
    assert ".website-builder-coolify-compose-details" in css
    assert ".website-builder-publishing-checkbox" in css
    assert ".website-builder-page-runtime-grid" in css
    assert ".website-builder-page-runtime-option" in css
    assert ".website-builder-directus-connection-backdrop" in css
    assert ".website-builder-directus-connection-form" in css
    assert "color: #0b1020;" in css
    assert "syncWebsiteBuilderSiteSelect" in script
    assert "selectWebsiteBuilderWorkspaceTab" in script
    assert "websiteBuilderSiteSelect" in bindings
    assert "websiteBuilderChatToggle" in bindings
    assert ".website-builder-chat-popout" in css
    assert ".website-builder-git-button" in css
    assert ".website-builder-git-panel" in css
    assert "websiteBuilderGitToggle" in bindings
    assert "websiteBuilderGitRefresh" in bindings
    assert "websiteBuilderGitAcceptHead" in bindings
    assert "websiteBuilderGitRestoreSelected" in bindings
    assert "websiteBuilderGitRevertSelected" in bindings
    assert "refreshWebsiteBuilderGitEdits" in script
    assert "runWebsiteBuilderGitRestoreSelected" in script
    assert "runWebsiteBuilderGitAcceptHead" in script
    assert "runWebsiteBuilderGitRevertSelected" in script
    assert "runWebsiteBuilderGitBackup" not in script
    assert "/api/applications/websites/site/git" in script
    assert "git apply -R --3way" in script
    assert "Manual merge hints:" in script
    assert "websiteBuilderVisitLocal" in bindings
    assert "websiteBuilderVisitDev" in bindings
    assert "websiteBuilderVisitRemoteProd" in bindings
    assert "websiteBuilderPublishRemote" in bindings
    assert "websiteBuilderArchive" in bindings
    assert "websiteBuilderPublishingUseLocalServer" in bindings
    assert "websiteBuilderPublishingSiteSlug" in bindings
    assert "websiteBuilderPublishingSshHost" in bindings
    assert "websiteBuilderPageRuntimeButtons" in bindings
    assert "websiteBuilderPublishDirectusUrl" in bindings
    assert "websiteBuilderPublishBlogDirectusCard" in bindings
    assert "websiteBuilderDirectusConnection" in bindings
    assert "websiteBuilderDirectusDatabaseVolume" in bindings


def test_website_builder_backend_routes_are_wired() -> None:
    routes = (ROOT / "main_computer" / "viewport_route_dispatch.py").read_text(encoding="utf-8")
    handlers = (ROOT / "main_computer" / "viewport_routes_applications.py").read_text(encoding="utf-8")

    assert '"/api/applications/websites/sites"' in routes
    assert '"/api/applications/websites/site"' in routes
    assert '"/api/applications/websites/site/save"' in routes
    assert '"/api/applications/websites/site/publish"' in routes
    assert '"/api/applications/websites/site/archive"' in routes
    assert '"/api/applications/websites/site/git"' in routes
    assert '"/api/applications/website-builder/chat"' in routes
    assert '"/api/applications/website-builder/chat/edit"' in routes
    assert '"/api/publishing/local-server/prepare"' not in routes
    assert "def _handle_websites_sites" in handlers
    assert "def _handle_websites_site_save" in handlers
    assert "def _handle_websites_site_publish" in handlers
    assert "def _handle_websites_site_archive" in handlers
    assert "def _handle_websites_site_git" in handlers
    assert "_website_builder_git_log_entries" in handlers
    assert "git apply -R --3way" in handlers
    assert "def _handle_website_builder_chat_edit" in handlers
    assert "def _handle_publishing_local_server_prepare" not in handlers
    assert "archive_website_project(" in handlers
    assert "create_local_platform_website_project(" in handlers
    assert "local_platform_registration" in handlers
    assert "allocate_unique_id=True" in handlers
    assert "save_website_project_files(" in handlers
    assert "saved_before_publish" in handlers
    assert "save_website_directus_connection(" in handlers
    assert "directus_connection" in handlers



def test_website_builder_chat_edit_route_is_locked_to_site_scope(tmp_path, monkeypatch) -> None:
    from main_computer.config import MainComputerConfig
    import main_computer.viewport_routes_applications as routes_applications
    from main_computer.viewport import ViewportServer
    from main_computer.website_project_manifest import list_website_projects

    list_website_projects(tmp_path)
    for rel in (
        "main_computer/web/applications/scripts/website-builder.js",
        "main_computer/web/applications/styles/website-builder.css",
        "main_computer/viewport_routes_applications.py",
        "main_computer/viewport_route_dispatch.py",
        "main_computer/website_project_manifest.py",
    ):
        source = ROOT / rel
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    calls: list[dict] = []

    def fake_generated_editor_pipeline(**kwargs):
        calls.append(kwargs)
        assert kwargs["repo"] == tmp_path.resolve()
        assert kwargs["site_id"] == "hub-site"
        assert kwargs["site_root"] == tmp_path / "runtime" / "websites" / "hub-site"
        output_dir = kwargs["output_dir"]
        output_dir.mkdir(parents=True, exist_ok=True)
        return {
            "ok": True,
            "terminal_state": "grounded_info_answer",
            "observed_terminal_class": "info",
            "answer": "You are editing the website for hub-site.",
            "evidence_files": ["builder.json", "index.html", "site.json"],
            "replacement_payloads": [],
            "artifact": None,
            "live_write": False,
        }

    monkeypatch.setattr(routes_applications, "run_website_builder_generated_editor_pipeline", fake_generated_editor_pipeline)

    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=tmp_path), verbose=False)
    server.debug_root = tmp_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path: str, payload: dict) -> dict:
        request = Request(
            base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        data = post(
            "/api/applications/website-builder/chat/edit",
            {
                "thread_id": "test-website-chat",
                "cell": {"id": "chat-website-scope", "type": "ai", "source": "What files can you see?"},
                "embedded_context": {"active_app": "website-builder", "site_id": "hub-site", "target_kind": "website-project", "target_id": "hub-site"},
                "embedded_context_source": {"active_app": "website-builder", "target_kind": "website-project", "target_id": "hub-site"},
                "mount_plugins": [{"id": "website-builder-edit", "enabled": True, "target_id": "hub-site", "site_id": "hub-site", "allowed_write_paths": ["main_computer/router.py"]}],
            },
        )
        assert data["ok"]
        output = data["output_cell"]
        assert output["metadata"]["editor_edit_mode"] == "website-builder"
        assert output["metadata"]["editor_intent"] == "answer"
        assert output["metadata"]["site_id"] == "hub-site"
        assert output["metadata"]["allowed_root"] == "runtime/websites/hub-site/"
        content = "\n".join(str(part.get("content", "")) for part in output["parts"])
        assert "Website Builder grounded answer" in content
        assert "You are editing the website for hub-site." in content
        assert "site.json" in content
        assert "No replacement payloads were produced" in content
        assert "main_computer/router.py" not in content
        assert "main_computer_test" not in content
        assert output["metadata"]["auto_apply"] is True
        assert output["metadata"]["apply_result"] is None
        assert calls and calls[0]["user_prompt"] == "What files can you see?"

        request = Request(
            base_url + "/api/applications/website-builder/chat/edit",
            data=json.dumps({
                "cell": {"id": "chat-website-scope", "type": "ai", "source": "What files can you see?"},
                "embedded_context": {"active_app": "website-builder", "site_id": "hub-site"},
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request, timeout=5)
            raise AssertionError("missing website-builder-edit plugin should fail")
        except HTTPError as exc:
            assert exc.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_website_builder_chat_edit_route_applies_generated_editor_payloads_by_default(tmp_path, monkeypatch) -> None:
    from main_computer.config import MainComputerConfig
    import main_computer.viewport_routes_applications as routes_applications
    from main_computer.viewport import ViewportServer
    from main_computer.website_project_manifest import list_website_projects

    list_website_projects(tmp_path)
    for rel in (
        "main_computer/web/applications/scripts/website-builder.js",
        "main_computer/web/applications/styles/website-builder.css",
        "main_computer/viewport_routes_applications.py",
        "main_computer/viewport_route_dispatch.py",
        "main_computer/website_project_manifest.py",
    ):
        source = ROOT / rel
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    index_file = tmp_path / "runtime" / "websites" / "hub-site" / "index.html"
    before = index_file.read_text(encoding="utf-8")
    replacement = before.replace("<h1>Hub Site</h1>", "<h1>Welcome to Arcstorm</h1>", 1)

    def fake_generated_editor_pipeline(**kwargs):
        output_dir = kwargs["output_dir"]
        replacement_file = output_dir / "13_replacement_files" / "index.html"
        replacement_file.parent.mkdir(parents=True, exist_ok=True)
        replacement_file.write_text(replacement, encoding="utf-8")
        return {
            "ok": True,
            "terminal_state": "promotable_edit_artifact",
            "observed_terminal_class": "edit",
            "promotion": {
                "ok": True,
                "target_file": "index.html",
                "replacement_file": str(replacement_file),
                "before_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
                "after_sha256": hashlib.sha256(replacement.encode("utf-8")).hexdigest(),
            },
            "artifact": {
                "path": str(output_dir / "rag_website_builder_real_edit_snapshot_patch.zip"),
                "mode": "snapshot_zip",
                "promotable": True,
                "replacement_files": [{"path": "index.html", "exists": True}],
                "dry_run_command": "python new_patch.py artifact.zip --dry-run",
            },
            "dry_run": {"ok": True, "command": "python new_patch.py artifact.zip --dry-run", "cwd": str(output_dir / "selected_site_workspace")},
            "changed_files": ["index.html"],
            "live_write": False,
        }

    monkeypatch.setattr(routes_applications, "run_website_builder_generated_editor_pipeline", fake_generated_editor_pipeline)

    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=tmp_path), verbose=False)
    server.debug_root = tmp_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path: str, payload: dict) -> dict:
        request = Request(
            base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        data = post(
            "/api/applications/website-builder/chat",
            {
                "thread_id": "test-website-chat",
                "cell": {"id": "chat-website-proposal", "type": "ai", "source": "Can you update the hero headline?"},
                "embedded_context": {"active_app": "website-builder", "site_id": "hub-site", "target_kind": "website-project", "target_id": "hub-site"},
                "embedded_context_source": {"active_app": "website-builder", "target_kind": "website-project", "target_id": "hub-site"},
                "mount_plugins": [{"id": "website-builder-edit", "enabled": True, "target_id": "hub-site", "site_id": "hub-site"}],
            },
        )
        assert data["ok"]
        metadata = data["output_cell"]["metadata"]
        proposal = metadata["proposal"]
        assert metadata["editor_intent"] == "apply_edit"
        assert metadata["generated_editor_terminal_state"] == "promotable_edit_artifact"
        assert metadata["auto_apply"] is True
        assert metadata["apply_result"]["ok"] is True
        assert metadata["allowed_roots"] == ["runtime/websites/hub-site/"]
        assert "main_computer/web/applications/scripts/website-builder.js" in metadata["builder_allowlist"]

        assert proposal["type"] == "website-builder-generated-editor-result"
        assert proposal["mode"] == "applied"
        assert proposal["changed_files"] == ["index.html"]
        assert proposal["artifact"]["promotable"] is True
        assert proposal["dry_run"]["ok"] is True

        content = "\n".join(str(part.get("content", "")) for part in data["output_cell"]["parts"])
        assert "Applied" in content
        assert "runtime/websites/hub-site/index.html" in content
        assert "Live files written" in content

        on_disk = index_file.read_text(encoding="utf-8")
        assert "Welcome to Arcstorm" in on_disk
        assert "<h1>Hub Site</h1>" not in on_disk
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_website_builder_rag_apply_validated_payload_writes_site_file_and_rejects_unsafe_payloads(tmp_path) -> None:
    from main_computer.config import MainComputerConfig
    from main_computer.viewport import ViewportServer
    from main_computer.website_project_manifest import list_website_projects

    list_website_projects(tmp_path)
    for rel in (
        "main_computer/web/applications/scripts/website-builder.js",
        "main_computer/web/applications/styles/website-builder.css",
        "main_computer/viewport_routes_applications.py",
        "main_computer/viewport_route_dispatch.py",
        "main_computer/website_project_manifest.py",
    ):
        source = ROOT / rel
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=tmp_path), verbose=False)
    server.debug_root = tmp_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path: str, payload: dict) -> dict:
        request = Request(
            base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_error(path: str, payload: dict) -> HTTPError:
        request = Request(
            base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request, timeout=5)
            raise AssertionError("request should have failed")
        except HTTPError as exc:
            return exc

    try:
        index_file = tmp_path / "runtime" / "websites" / "hub-site" / "index.html"
        before = index_file.read_text(encoding="utf-8")
        replacement = before.replace("<h1>Hub Site</h1>", "<h1>Welcome to Arcstorm</h1>", 1)
        payload = {
            "thread_id": "test-website-rag-apply",
            "embedded_context": {"active_app": "website-builder", "site_id": "hub-site", "target_kind": "website-project", "target_id": "hub-site"},
            "embedded_context_source": {"active_app": "website-builder", "target_kind": "website-project", "target_id": "hub-site"},
            "mount_plugins": [{"id": "website-builder-edit", "enabled": True, "target_id": "hub-site", "site_id": "hub-site"}],
            "payloads": [
                {
                    "path": "runtime/websites/hub-site/index.html",
                    "operation": "modify",
                    "original_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
                    "replacement_sha256": hashlib.sha256(replacement.encode("utf-8")).hexdigest(),
                    "replacement_text": replacement,
                }
            ],
        }

        data = post("/api/applications/website-builder/chat/apply-rag-proposal", payload)
        assert data["ok"]
        assert data["mode"] == "rag-validated-live-apply"
        assert data["files"][0]["path"] == "runtime/websites/hub-site/index.html"
        assert "Welcome to Arcstorm" in index_file.read_text(encoding="utf-8")

        stale = post_error(
            "/api/applications/website-builder/chat/apply-rag-proposal",
            {
                **payload,
                "payloads": [
                    {
                        "path": "runtime/websites/hub-site/index.html",
                        "operation": "modify",
                        "original_sha256": "not-the-current-hash",
                        "replacement_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
                        "replacement_text": before,
                    }
                ],
            },
        )
        assert stale.code == 400
        assert "Welcome to Arcstorm" in index_file.read_text(encoding="utf-8")

        escaped = post_error(
            "/api/applications/website-builder/chat/apply-rag-proposal",
            {
                **payload,
                "payloads": [
                    {
                        "path": "main_computer/router.py",
                        "operation": "modify",
                        "original_sha256": None,
                        "replacement_sha256": hashlib.sha256(b"").hexdigest(),
                        "replacement_text": "",
                    }
                ],
            },
        )
        assert escaped.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)



def test_website_builder_chat_edit_auto_applies_validated_payloads(tmp_path, monkeypatch) -> None:
    from main_computer.config import MainComputerConfig
    import main_computer.viewport_routes_applications as routes_applications
    from main_computer.viewport import ViewportServer
    from main_computer.website_project_manifest import list_website_projects

    list_website_projects(tmp_path)
    for rel in (
        "main_computer/web/applications/scripts/website-builder.js",
        "main_computer/web/applications/styles/website-builder.css",
        "main_computer/viewport_routes_applications.py",
        "main_computer/viewport_route_dispatch.py",
        "main_computer/website_project_manifest.py",
    ):
        source = ROOT / rel
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    index_file = tmp_path / "runtime" / "websites" / "hub-site" / "index.html"
    before = index_file.read_text(encoding="utf-8")
    replacement = before.replace("<h1>Hub Site</h1>", "<h1>Welcome to Arcstorm</h1>", 1)

    def fake_generated_editor_pipeline(**kwargs):
        output_dir = kwargs["output_dir"]
        replacement_file = output_dir / "13_replacement_files" / "index.html"
        replacement_file.parent.mkdir(parents=True, exist_ok=True)
        replacement_file.write_text(replacement, encoding="utf-8")
        return {
            "ok": True,
            "terminal_state": "promotable_edit_artifact",
            "observed_terminal_class": "edit",
            "promotion": {
                "ok": True,
                "target_file": "index.html",
                "replacement_file": str(replacement_file),
                "before_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
                "after_sha256": hashlib.sha256(replacement.encode("utf-8")).hexdigest(),
            },
            "artifact": {
                "path": str(output_dir / "rag_website_builder_real_edit_snapshot_patch.zip"),
                "mode": "snapshot_zip",
                "promotable": True,
                "replacement_files": [{"path": "index.html", "exists": True}],
                "dry_run_command": "python new_patch.py artifact.zip --dry-run",
            },
            "dry_run": {"ok": True, "command": "python new_patch.py artifact.zip --dry-run", "cwd": str(output_dir / "selected_site_workspace")},
            "changed_files": ["index.html"],
            "live_write": False,
        }

    monkeypatch.setattr(routes_applications, "run_website_builder_generated_editor_pipeline", fake_generated_editor_pipeline)

    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=tmp_path), verbose=False)
    server.debug_root = tmp_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path: str, payload: dict) -> dict:
        request = Request(
            base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        data = post(
            "/api/applications/website-builder/chat",
            {
                "thread_id": "test-website-auto-apply",
                "auto_apply": True,
                "live_apply": True,
                "cell": {"id": "chat-website-auto-apply", "type": "ai", "source": "change the hero headline to Welcome to Arcstorm"},
                "embedded_context": {"active_app": "website-builder", "site_id": "hub-site", "target_kind": "website-project", "target_id": "hub-site"},
                "embedded_context_source": {"active_app": "website-builder", "target_kind": "website-project", "target_id": "hub-site"},
                "mount_plugins": [{"id": "website-builder-edit", "enabled": True, "target_id": "hub-site", "site_id": "hub-site", "auto_apply": True, "live_apply": True}],
            },
        )

        assert data["ok"]
        output = data["output_cell"]
        assert output["metadata"]["editor_intent"] == "apply_edit"
        assert output["metadata"]["auto_apply"] is True
        assert output["metadata"]["apply_result"]["ok"] is True
        assert output["metadata"]["apply_result"]["mode"] == "generated-editor-live-apply"
        assert output["metadata"]["proposal"]["mode"] == "applied"
        assert output["metadata"]["apply_result"]["files"][0]["path"] == "runtime/websites/hub-site/index.html"
        after = index_file.read_text(encoding="utf-8")
        assert "Welcome to Arcstorm" in after
        assert after != before
        content = "\n".join(str(part.get("content", "")) for part in output["parts"])
        assert "Applied" in content
        assert "Website Builder generated-editor edit wrote validated replacement files" in content
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_website_builder_rag_smoke_requires_explicit_site_id_when_multiple_projects(tmp_path) -> None:
    import pytest

    from main_computer.website_builder_rag_pipeline import SmokeFailure, select_site_id

    for site_id in ("hub-site", "johnrraymond"):
        site_root = tmp_path / "runtime" / "websites" / site_id
        site_root.mkdir(parents=True, exist_ok=True)
        (site_root / "site.json").write_text(json.dumps({"id": site_id}), encoding="utf-8")

    assert select_site_id(tmp_path, "johnrraymond") == "johnrraymond"
    assert select_site_id(tmp_path, None, allow_ambiguous_default=True) == "hub-site"

    with pytest.raises(SmokeFailure) as excinfo:
        select_site_id(tmp_path, None)
    message = str(excinfo.value)
    assert "Multiple Website Builder sites are available" in message
    assert "--site-id" in message
    assert "hub-site" in message
    assert "johnrraymond" in message


def test_website_builder_mounted_chat_rejects_missing_site_id_when_multiple_projects(tmp_path) -> None:
    from main_computer.config import MainComputerConfig
    from main_computer.viewport import ViewportServer
    from main_computer.website_project_manifest import create_website_project, list_website_projects

    list_website_projects(tmp_path)
    create_website_project(tmp_path, "johnrraymond", "John R Raymond")

    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=tmp_path), verbose=False)
    server.debug_root = tmp_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    request = Request(
        base_url + "/api/applications/website-builder/chat",
        data=json.dumps({
            "cell": {"id": "chat-website-missing-site", "type": "ai", "source": "change the hero headline"},
            "embedded_context": {"active_app": "website-builder", "target_kind": "website-project"},
            "embedded_context_source": {"active_app": "website-builder", "target_kind": "website-project"},
            "mount_plugins": [{"id": "website-builder-edit", "enabled": True}],
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        try:
            urlopen(request, timeout=5)
            raise AssertionError("missing site id should fail when multiple sites exist")
        except HTTPError as exc:
            assert exc.code == 400
            body = exc.read().decode("utf-8")
            assert "Missing active Website Builder site id" in body
            assert "hub-site" in body
            assert "johnrraymond" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_website_builder_registry_lane_payload_uses_local_platform_manifest(tmp_path) -> None:
    from main_computer.website_project_manifest import list_website_projects, save_website_publish_target

    hub = next(project for project in list_website_projects(tmp_path) if project.id == "hub-site")
    payload = hub.to_dict(tmp_path)

    assert payload["local_platform"]["lanes"]["local"]["service"] == "hub-local"
    assert payload["local_platform"]["lanes"]["local"]["url"] == "http://0.0.0.0:18080/"
    assert payload["local_platform"]["lanes"]["dev"]["service"] == "hub-dev"
    assert payload["local_platform"]["lanes"]["dev"]["url"] == "http://0.0.0.0:18082/"
    assert payload["publish_targets"]["remote_prod"]["accepted_at"] == ""

    save_website_publish_target(
        tmp_path,
        "hub-site",
        "remote_prod",
        project="johnrraymond",
        site_slug="johnrraymond",
        source_path="runtime/websites/hub-site",
        remote_host="root@publish.greatlibrary.io",
        remote_root="/srv/main-computer/sites",
        environment="production",
        domain="",
    )
    accepted_hub = next(project for project in list_website_projects(tmp_path) if project.id == "hub-site")
    accepted_payload = accepted_hub.to_dict(tmp_path)
    assert accepted_payload["publish_targets"]["remote_prod"]["accepted_at"]

def test_website_builder_python_assets_compile() -> None:
    for path in (
        ROOT / "main_computer" / "website_project_manifest.py",
        ROOT / "main_computer" / "viewport_routes_applications.py",
        ROOT / "main_computer" / "local_platform_lifecycle.py",
        ROOT / "tools" / "local-platform" / "publish-website.py",
        ROOT / "tools" / "local-platform" / "website-docker.py",
        ROOT / "deploy" / "local-platform" / "site-server" / "app.py",
    ):
        py_compile.compile(str(path), doraise=True)



def test_website_builder_api_reports_publish_payload_errors_instead_of_request_failed_200() -> None:
    script = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "website-builder.js").read_text(encoding="utf-8")

    assert "function websiteBuilderApiErrorMessage(payload, response)" in script
    assert "result.cms_verify_error" in script
    assert "failedCms.service" in script
    assert "throw new Error(websiteBuilderApiErrorMessage(payload, response));" in script
