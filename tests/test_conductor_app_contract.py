from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
DOM_BINDINGS_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings.js"
NAVIGATION_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "navigation.js"
APP_ROUTING_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "app-routing.js"
CONDUCTOR_HTML = ROOT / "main_computer" / "web" / "applications" / "apps" / "conductor.html"
CONDUCTOR_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "conductor.js"
CONDUCTOR_CSS = ROOT / "main_computer" / "web" / "applications" / "styles" / "conductor.css"
DISPATCH = ROOT / "main_computer" / "viewport_route_dispatch.py"
SERVER = ROOT / "main_computer" / "viewport_server.py"
CLI = ROOT / "main_computer" / "cli.py"


def test_conductor_app_is_routed_and_visible() -> None:
    html = APPLICATIONS_HTML.read_text(encoding="utf-8")
    bindings = DOM_BINDINGS_JS.read_text(encoding="utf-8")
    navigation = NAVIGATION_JS.read_text(encoding="utf-8")
    routing = APP_ROUTING_JS.read_text(encoding="utf-8")

    assert 'href="/applications/conductor" data-app="conductor"' in html
    assert "<!-- @include applications/apps/conductor.html -->" in html
    assert "<!-- @include applications/styles/conductor.css -->" in html
    assert "<!-- @include applications/scripts/conductor.js -->" in html
    assert "<!-- @include applications/scripts/dom-bindings/conductor.js -->" in bindings
    assert 'conductor: ["Conductor"' in navigation
    assert '{app: "conductor", glyph: "K", title: "Conductor", summary: "scheduled worker"}' in navigation
    assert 'const isConductor = normalizedApp === "conductor";' in routing
    assert 'conductorApp.style.display = isConductor ? "grid" : "none"' in routing
    assert "initConductorApp();" in routing


def test_conductor_ui_has_schedule_dns_ssl_and_key_surfaces() -> None:
    html = CONDUCTOR_HTML.read_text(encoding="utf-8")
    js = CONDUCTOR_JS.read_text(encoding="utf-8")
    css = CONDUCTOR_CSS.read_text(encoding="utf-8")

    expected_ids = [
        'id="conductor-app"',
        'id="conductor-status"',
        'id="conductor-counts"',
        'id="conductor-refresh"',
        'id="conductor-run-due"',
        'id="conductor-action-form"',
        'id="conductor-action"',
        'id="conductor-run-at"',
        'id="conductor-confirm"',
        'id="conductor-payload"',
        'id="conductor-jobs"',
        'id="conductor-records"',
        'id="conductor-script-area"',
        'id="conductor-script-search"',
        'id="conductor-script-select"',
        'id="conductor-scripts"',
        'id="conductor-fill-selected-script"',
    ]
    for expected_id in expected_ids:
        assert expected_id in html

    assert "dns.record.upsert" in html
    assert "local.secret.generate" in html
    assert "ssl.key.generate" in html
    assert "script.run" in html
    assert 'const CONDUCTOR_STATUS_ENDPOINT = "/api/applications/conductor/status";' in js
    assert 'const CONDUCTOR_ACTION_ENDPOINT = "/api/applications/conductor/action";' in js
    assert 'const CONDUCTOR_RUN_DUE_ENDPOINT = "/api/applications/conductor/run-due";' in js
    assert "async function refreshConductor" in js
    assert "async function submitConductorAction" in js
    assert "function renderConductorScriptAreas" in js
    assert "function renderConductorScripts" in js
    assert "function conductorFilteredScripts" in js
    assert "function renderConductorSuggestedInvocations" in js
    assert "function renderConductorQuarantineNote" in js
    assert "function conductorSelectedScriptPayload" in js
    assert "function conductorScriptBadge" in js
    assert 'class="conductor-script-badges"' in js
    assert 'class="conductor-command-template"' in js
    assert ".conductor-app" in css
    assert ".conductor-script-examples" in css
    assert ".conductor-quarantine-badge" in css
    assert ".conductor-quarantine-note" in css
    assert ".conductor-script-list" in css
    assert "conductor-result-card" in html
    assert html.index('class="conductor-card conductor-result-card"') < html.index('<section class="conductor-main">')
    assert ".conductor-script-badge" in css
    assert ".conductor-command-template" in css
    assert ".conductor-result-card" in css
    assert "grid-template-columns: minmax(20rem, clamp(20rem, 36vw, 24rem)) minmax(30rem, 1fr);" in css
    assert "overflow-x: auto;" in css
    assert ".conductor-main {\n  min-width: 30rem;" in css
    assert ".conductor-app *::after" in css
    assert "box-sizing: border-box;" in css


def test_conductor_backend_routes_and_cli_are_registered() -> None:
    dispatch = DISPATCH.read_text(encoding="utf-8")
    server = SERVER.read_text(encoding="utf-8")
    cli = CLI.read_text(encoding="utf-8")

    assert 'route_path == "/api/applications/conductor/status"' in dispatch
    assert 'route_path == "/api/applications/conductor/action"' in dispatch
    assert 'route_path == "/api/applications/conductor/run-due"' in dispatch
    assert "ViewportConductorRoutesMixin" in server
    assert "self.conductor = ConductorService(self.debug_root)" in server
    assert 'sub.add_parser(\n        "conductor"' in cli
    assert 'conductor_submit.add_argument("--action"' in cli
    assert 'conductor_due = conductor_sub.add_parser("run-due"' in cli
