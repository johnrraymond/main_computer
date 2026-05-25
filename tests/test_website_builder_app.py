from __future__ import annotations

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


def test_website_builder_frontend_assets_define_save_and_publish_controls() -> None:
    app = (ROOT / "main_computer" / "web" / "applications" / "apps" / "website-builder.html").read_text(encoding="utf-8")
    script = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "website-builder.js").read_text(encoding="utf-8")

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
    assert "website-builder-preview-draft" in app
    assert "website-builder-preview-local" in app
    assert "website-builder-preview-dev" in app
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
    assert 'data-website-builder-tab="design"' in app
    assert 'data-website-builder-panel="source"' in app
    assert "grid-template-columns: minmax(720px, 1fr) 320px" in css
    assert ".website-site-card span" in css
    assert ".website-builder-coolify-compose-details" in css
    assert ".website-builder-publishing-checkbox" in css
    assert ".website-builder-directus-connection-backdrop" in css
    assert ".website-builder-directus-connection-form" in css
    assert "color: #0b1020;" in css
    assert "syncWebsiteBuilderSiteSelect" in script
    assert "selectWebsiteBuilderWorkspaceTab" in script
    assert "websiteBuilderSiteSelect" in bindings
    assert "websiteBuilderVisitLocal" in bindings
    assert "websiteBuilderVisitDev" in bindings
    assert "websiteBuilderVisitRemoteProd" in bindings
    assert "websiteBuilderPublishRemote" in bindings
    assert "websiteBuilderArchive" in bindings
    assert "websiteBuilderPublishingUseLocalServer" in bindings
    assert "websiteBuilderPublishingSiteSlug" in bindings
    assert "websiteBuilderPublishingSshHost" in bindings
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
    assert '"/api/applications/website-builder/chat/edit"' in routes
    assert '"/api/publishing/local-server/prepare"' not in routes
    assert "def _handle_websites_sites" in handlers
    assert "def _handle_websites_site_save" in handlers
    assert "def _handle_websites_site_publish" in handlers
    assert "def _handle_websites_site_archive" in handlers
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



def test_website_builder_chat_edit_route_is_locked_to_site_scope(tmp_path) -> None:
    from main_computer.config import MainComputerConfig
    from main_computer.viewport import ViewportServer
    from main_computer.website_project_manifest import list_website_projects

    list_website_projects(tmp_path)
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
        assert output["metadata"]["site_id"] == "hub-site"
        assert output["metadata"]["allowed_root"] == "runtime/websites/hub-site/"
        content = "\n".join(str(part.get("content", "")) for part in output["parts"])
        assert "runtime/websites/hub-site/site.json" in content
        assert "proposal-only" in content
        assert "main_computer/router.py" not in content
        assert "main_computer_test" not in content
        assert output["metadata"]["auto_apply"] is False

        from main_computer.models import ChatResponse

        class FakeMountedProvider:
            name = "fake-mounted-provider"
            model = "fake-mounted-model"

        class FakeMountedComputer:
            provider = FakeMountedProvider()

            def chat_console_ai(self, source: str, attachments: list | None = None) -> ChatResponse:
                return ChatResponse(
                    content=f"inline scoped AI ran: {'Allowed root: `runtime/websites/hub-site/`' in source}",
                    provider="fake-mounted-provider",
                    model="fake-mounted-model",
                )

        server.computer = FakeMountedComputer()
        ai_data = post(
            "/api/applications/website-builder/chat/edit",
            {
                "thread_id": "test-website-chat",
                "cell": {"id": "chat-website-ai", "type": "ai", "source": "Say hello from this website."},
                "embedded_context": {"active_app": "website-builder", "site_id": "hub-site", "target_kind": "website-project", "target_id": "hub-site"},
                "embedded_context_source": {"active_app": "website-builder", "target_kind": "website-project", "target_id": "hub-site"},
                "mount_plugins": [{"id": "website-builder-edit", "enabled": True, "target_id": "hub-site", "site_id": "hub-site"}],
            },
        )
        ai_content = "\n".join(str(part.get("content", "")) for part in ai_data["output_cell"]["parts"])
        assert "inline scoped AI ran: True" in ai_content
        assert ai_data["output_cell"]["metadata"]["scope_card"] is False

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
