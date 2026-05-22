from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _asset(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _script_function_body(script: str, function_name: str) -> str:
    marker = f"async function {function_name}"
    start = script.index(marker)
    next_function = script.index("\n    function escapePreviewStyle", start)
    return script[start:next_function]


def test_blog_backend_product_opens_layered_runtime_wizard() -> None:
    app = _asset("main_computer/web/applications/apps/website-builder.html")
    script = _asset("main_computer/web/applications/scripts/website-builder.js")
    bindings = _asset("main_computer/web/applications/scripts/dom-bindings/websites.js")
    css = _asset("main_computer/web/applications/styles/website-builder.css")

    assert 'data-website-builder-backend-product="blog"' in app
    assert 'id="website-builder-blog-wizard"' in app
    assert 'id="website-builder-blog-layer-stack"' in app
    assert 'id="website-builder-blog-install-order"' in app
    assert 'id="website-builder-blog-runtime-actions"' in app
    assert "Configure Blog Runtime" in app
    for removed_footer in [
        "website-builder-blog-install-plan",
        "website-builder-blog-dry-run",
        "website-builder-blog-apply",
        "website-builder-blog-commit",
        "View Plan",
        "Run Dry-Run",
        "Apply Blog Runtime",
        "Commit Blog Runtime",
    ]:
        assert removed_footer not in app
    assert "Blog requires Directus and SQLite. Configure Blog Runtime prepares the local Directus and SQLite dependencies." in app
    assert 'data-mc-widget-id="website-builder.backend-product-blog"' in app
    assert 'data-mc-widget-id="website-builder.blog-runtime-wizard"' in app
    assert 'data-mc-widget-id="website-builder.blog-runtime-configure"' in app

    assert "websiteBuilderBlogLayerInstallOrder" in script
    assert '["database", "cms", "blog"]' in script
    assert "openWebsiteBuilderBlogInstallWizard" in script
    assert "openWebsiteBuilderBlogConfigureFlow().catch" in script
    assert "runWebsiteBuilderBlogInstallStack" in script
    assert "renderWebsiteBuilderBlogRuntime" in script
    for removed_label in [
        "Open Blog",
        "Open Directus CMS",
        "Edit Blog Code",
        "View Runtime Config",
    ]:
        assert removed_label not in script
    assert "Loaded Blog runtime requirements." in script
    assert "setWebsiteBuilderMcWidget" in script
    assert "website-builder.blog-runtime.layer." in script
    assert "blogRuntimeWizard" in script
    assert "blogInstallWizard" not in script

    assert "websiteBuilderBlogWizard" in bindings
    assert "websiteBuilderBlogInstallConfirm" in bindings
    assert "websiteBuilderBlogRuntimeActions" in bindings
    assert "websiteBuilderBlogInstallPlan" not in bindings
    assert "websiteBuilderBlogDryRun" not in bindings
    assert "websiteBuilderBlogApply" not in bindings
    assert "websiteBuilderBlogCommit" not in bindings
    assert ".website-builder-blog-wizard-backdrop" in css
    assert ".website-builder-blog-layer-stack" in css
    assert ".website-builder-blog-runtime-actions" in css


def test_blog_wizard_presents_deploy_verified_directus_without_sqlite_overwrite_controls() -> None:
    app = _asset("main_computer/web/applications/apps/website-builder.html")
    script = _asset("main_computer/web/applications/scripts/website-builder.js")
    bindings = _asset("main_computer/web/applications/scripts/dom-bindings/websites.js")

    assert "Blog requires Directus and SQLite. Configure Blog Runtime prepares the local Directus and SQLite dependencies." in app
    assert "Existing DB management tools will come later." in app
    assert "Configure Blog Runtime" in app
    assert "SQLite already exists" not in app
    assert "Keep Existing SQLite" not in app
    assert "Overwrite SQLite and Continue" not in app
    assert "Install Blog" not in app
    assert "Configure recommended stack" not in app

    assert "websiteBuilderBlogIntentEndpoint" in script
    assert "/blog/intent" in script
    assert "websiteBuilderBlogPersistIntentApi" in script
    assert "websiteBuilderDirectusConnectionForBlogConfigure" in script
    assert "openWebsiteBuilderBlogConfigureFlow" in script
    assert 'context: "blog_configure"' in script
    assert "pendingDirectusConnection" in script
    assert "Directus storage choice captured for Blog configuration." in script
    assert "Blog intent saved. Installing local runtime layers..." in script
    assert "Blog runtime layers configured. Local Directus setup ran during Configure Blog Runtime." in script
    assert "websiteBuilderBlogLayerInstallEndpoint" in script
    assert "/blog/layers/" in script
    assert "Directus is required for Blog. Configure Blog Runtime prepares the local service" in script
    assert "pending_deploy" in script
    assert "configured" in script
    assert "Configure Blog Runtime prepares local runtime dependencies. Existing DB management tools will come later." in script
    assert "openWebsiteBuilderBlogSqliteGuard" not in script
    assert "websiteBuilderBlogSqliteOverwrite" not in script

    assert "websiteBuilderBlogSqliteGuard" not in bindings
    assert "websiteBuilderBlogSqliteOverwriteCheck" not in bindings
    assert "websiteBuilderBlogSqliteOverwrite" not in bindings


def test_blog_backend_hooks_have_routes_with_frontend_fallback() -> None:
    script = _asset("main_computer/web/applications/scripts/website-builder.js")
    dispatch = _asset("main_computer/viewport_route_dispatch.py")
    routes = _asset("main_computer/viewport_routes_applications.py")

    assert "websiteBuilderBlogAssumptionsEndpoint" in script
    assert "/blog/install-assumptions" in script
    assert "websiteBuilderBlogGetAssumptions" in script
    assert "websiteBuilderBlogInstallLayerApi" in script
    assert "websiteBuilderBlogPersistIntentApi" in script
    assert "frontend_fixture_until_backend_exists" in script
    assert "Backend assumption hook not available yet; using frontend fixture." in script

    assert "/blog/install-assumptions" in dispatch
    assert "/blog/intent" in dispatch
    assert "/blog/layers/" in dispatch
    assert "_handle_blog_install_assumptions" in routes
    assert "_handle_blog_intent" in routes
    assert "_handle_blog_layer_install" in routes
    assert "persist_blog_intent" in routes
    assert "install_blog_layer" in routes



def test_configure_blog_runtime_persists_intent_then_installs_each_layer() -> None:
    script = _asset("main_computer/web/applications/scripts/website-builder.js")
    body = _script_function_body(script, "runWebsiteBuilderBlogInstallStack")

    directus_call = body.index("websiteBuilderDirectusConnectionForBlogConfigure(websiteBuilderStateModel.selectedSite, {")
    intent_call = body.index("websiteBuilderBlogPersistIntentApi(siteId, payload)")
    loop_call = body.index("for (const layerId of websiteBuilderBlogLayerInstallOrder)")
    layer_call = body.index("websiteBuilderBlogInstallLayerApi(siteId, layerId, layerPayload)")

    assert directus_call < intent_call < loop_call < layer_call
    assert "directus_connection: directusConnection" in body
    assert "keep_existing: true" in body
    assert "Blog intent saved. Installing local runtime layers..." in body
    assert "Installing ${layerLabel} layer..." in body
    assert "setup_local_directus: true" in body
    assert "layerResult.contract" in body
    assert "websiteBuilderBlogSetupActivityLines(layerResult)" in body
    assert "Directus compose returned" in script
    assert "Directus setup targeted service" in script
    assert "Blog runtime layers configured. Local Directus setup ran during Configure Blog Runtime." in body
    assert "Blog intent saved to site.json. Deploy has not started." not in body

def test_directus_modal_only_opens_from_configure_blog_runtime_button() -> None:
    script = _asset("main_computer/web/applications/scripts/website-builder.js")

    product_handler = script[
        script.index('websiteBuilderBackendProductButtons.forEach((button) => {'):
        script.index('websiteBuilderBlogWizardClose?.addEventListener', script.index('websiteBuilderBackendProductButtons.forEach((button) => {'))
    ]
    blog_open_body = script[
        script.index("async function openWebsiteBuilderBlogConfigureFlow"):
        script.index("async function runWebsiteBuilderBlogInstallStack", script.index("async function openWebsiteBuilderBlogConfigureFlow"))
    ]
    configure_body = _script_function_body(script, "runWebsiteBuilderBlogInstallStack")
    publish_body = script[
        script.index("async function publishWebsiteBuilderSite"):
        script.index("async function archiveWebsiteBuilderSite", script.index("async function publishWebsiteBuilderSite"))
    ]

    assert "openWebsiteBuilderBlogConfigureFlow().catch" in product_handler
    assert "websiteBuilderDirectusConnectionForBlogConfigure" not in blog_open_body
    assert "openWebsiteBuilderBlogInstallWizard(options)" in blog_open_body

    assert "websiteBuilderDirectusConnectionForBlogConfigure(websiteBuilderStateModel.selectedSite" in configure_body
    assert 'context: "blog_configure"' in script
    assert "websiteBuilderDirectusConnectionForLocalPublish" in publish_body
    assert "websiteBuilderDirectusConnectionConfirmed" not in publish_body
    assert 'context: "local_publish"' not in publish_body


def test_deploy_button_opens_preflight_modal_before_running_deploy() -> None:
    app = _asset("main_computer/web/applications/apps/website-builder.html")
    script = _asset("main_computer/web/applications/scripts/website-builder.js")
    bindings = _asset("main_computer/web/applications/scripts/dom-bindings/websites.js")
    css = _asset("main_computer/web/applications/styles/website-builder.css")

    assert 'id="website-builder-deploy-preflight"' in app
    assert 'id="website-builder-deploy-preflight-command"' in app
    assert 'id="website-builder-deploy-preflight-ack"' in app
    assert 'id="website-builder-deploy-preflight-confirm"' in app
    assert "Needs acknowledgement" in app
    assert "I understand this deploy will recreate the selected site container." in app

    assert "deployPreflight" in script
    assert "openWebsiteBuilderDeployPreflight" in script
    assert "websiteBuilderPublishApi(siteId, lane, {dryRun: true})" in script
    assert 'payload.dry_run = true' in script
    assert "Docker will force-recreate" in script
    assert 'if (lane === "dev" && !options.skipPreflight)' in script
    assert 'publishWebsiteBuilderSite(lane, {skipPreflight: true})' in script
    assert "Deploy requires acknowledgement before it can continue." in script

    assert "websiteBuilderDeployPreflight" in bindings
    assert "websiteBuilderDeployPreflightAck" in bindings
    assert "websiteBuilderDeployPreflightConfirm" in bindings
    assert ".website-builder-deploy-preflight-backdrop" in css
    assert ".website-builder-deploy-preflight-warning" in css
