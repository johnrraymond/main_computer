from __future__ import annotations

from html.parser import HTMLParser
import re
from pathlib import Path

from main_computer.email_client import EmailClientConfigError, normalize_email_check_config, public_email_check_summary
from main_computer.viewport import APPLICATIONS_INDEX_HTML, _application_route_target


class _EmailComponentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.components: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(attrs)

    def _record(self, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name: value for name, value in attrs if name}
        if str(attr_map.get("data-mc-component-id", "")).startswith("email."):
            self.components.append(attr_map)


def test_email_application_is_routed_and_included() -> None:
    assert _application_route_target("/applications/email") == "email"
    assert _application_route_target("/apps/email") == "email"
    assert _application_route_target("/app/email") == "email"

    expected = [
        'href="/applications/email"',
        'data-app="email"',
        'id="email-app"',
        "IMAP / POP3 mail",
        "Search your mail",
        "New message",
        'data-email-tab="mail"',
        'data-email-tab="config"',
        'id="email-list-view"',
        'id="email-thread-view"',
        'id="email-back-to-list"',
        'id="email-compose-modal"',
        'id="email-reply-form"',
        'id="email-raw-account-list"',
        "IMAP / POP3 accounts",
        "Add account",
        "Check mail",
        "Outlook.com",
        "Microsoft 365 / Exchange Online",
        "iCloud Mail",
        "AOL Mail",
        "Fastmail",
        "Zoho Mail",
        "Proton Mail Bridge",
        "function initEmailApp()",
        "emailCheckServerAccount",
        "function emailSaveInlineReply",
        "function emailSetComposeModalOpen",
        "/api/applications/email/check",
        "imap.gmail.com",
        "outlook.office365.com",
        "imap.mail.me.com",
        "imap.fastmail.com",
        'data-mc-component-id="email.root"',
        'data-mc="app"',
        'class="email-shell app-widget mc-app-shell"',
        'class="email-client-frame mc-app-workspace"',
    ]
    for text in expected:
        assert text in APPLICATIONS_INDEX_HTML


def test_email_has_no_provider_oauth_or_special_provider_panels() -> None:
    banned = [
        "oauth",
        "OAuth",
        "start_google_oauth",
        "gmail_client",
        "/api/applications/email/oauth",
        "/api/applications/email/gmail",
        "email-gmail-account-list",
        "email-yahoo-account-list",
        "email-gmail-address-input",
        "email-yahoo-address-input",
        "email-yahoo-client-id-input",
        "Connect Gmail",
        "Add Yahoo account",
        "Gmail accounts",
        "Yahoo accounts",
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://api.login.yahoo.com/oauth2/request_auth",
    ]
    for text in banned:
        assert text not in APPLICATIONS_INDEX_HTML


def test_email_default_layout_is_list_first_without_internal_specimen_copy() -> None:
    assert "MCEL email specimen is ready" not in APPLICATIONS_INDEX_HTML
    assert "mcel-lab@maincomputer.local" not in APPLICATIONS_INDEX_HTML
    assert "email-mcel-proof" not in APPLICATIONS_INDEX_HTML
    for fake in ["LinkedIn Job Alerts", "Dell Notifications", "Virtual Vacations", "City Bank", "Morning Brief"]:
        assert fake not in APPLICATIONS_INDEX_HTML
    assert "data-email-category" not in APPLICATIONS_INDEX_HTML
    assert "Offers" not in APPLICATIONS_INDEX_HTML
    assert "Social" not in APPLICATIONS_INDEX_HTML
    assert "Newsletters" not in APPLICATIONS_INDEX_HTML
    assert "Email is IMAP / POP3 only now" in APPLICATIONS_INDEX_HTML
    assert 'const emailSeedAccounts = Object.freeze([]);' in APPLICATIONS_INDEX_HTML
    assert 'main-computer-email-app-v6' in APPLICATIONS_INDEX_HTML

    assert 'id="email-list-view"' in APPLICATIONS_INDEX_HTML
    assert 'id="email-thread-view"' in APPLICATIONS_INDEX_HTML
    assert 'hidden\n            data-mc="panel"' in APPLICATIONS_INDEX_HTML
    assert 'class="email-message-item ${message.id === emailAppState.selectedMessageId ? "active" : ""} ${message.unread ? "unread" : ""}"' in APPLICATIONS_INDEX_HTML
    assert 'class="email-row-sender"' in APPLICATIONS_INDEX_HTML
    assert 'class="email-row-subject"' in APPLICATIONS_INDEX_HTML
    assert 'class="email-row-date"' in APPLICATIONS_INDEX_HTML
    assert 'emailSetMailView("thread");' in APPLICATIONS_INDEX_HTML
    assert 'function emailSetMailView' in APPLICATIONS_INDEX_HTML
    assert 'class="email-compose-modal"' in APPLICATIONS_INDEX_HTML
    assert 'function emailSaveInlineReply' in APPLICATIONS_INDEX_HTML
    assert 'date.toDateString() === now.toDateString()' in APPLICATIONS_INDEX_HTML
    assert 'date.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"})' in APPLICATIONS_INDEX_HTML


def test_email_config_is_imap_pop_only() -> None:
    assert 'data-email-config-tab=' not in APPLICATIONS_INDEX_HTML
    assert 'data-email-config-panel="raw"' in APPLICATIONS_INDEX_HTML
    assert 'activeConfigTab: "raw"' in APPLICATIONS_INDEX_HTML
    assert 'id="email-raw-account-list"' in APPLICATIONS_INDEX_HTML
    assert "IMAP / POP3 accounts" in APPLICATIONS_INDEX_HTML
    assert "Protocol" in APPLICATIONS_INDEX_HTML
    assert '<option value="imap">IMAP</option>' in APPLICATIONS_INDEX_HTML
    assert '<option value="pop3">POP3</option>' in APPLICATIONS_INDEX_HTML
    assert "Provider-specific sign-in integrations have been removed." in APPLICATIONS_INDEX_HTML


def test_email_mcel_is_the_layout_layer() -> None:
    required_layout_contract = [
        'data-mcel-layout="email-app"',
        'data-mcel-layout="email-shell"',
        'data-mcel-layout="email-mail-workspace"',
        'data-mcel-layout="email-config-workspace"',
        'data-mcel-layout-region="email-account-rail"',
        'data-mcel-layout-region="email-folder-nav"',
        'data-mcel-layout-region="email-message-workspace"',
        'data-mcel-layout-region="email-empty-state"',
        'data-mcel-layout-region="email-server-form"',
        'data-mcel-layout-region="email-server-fields"',
    ]
    for text in required_layout_contract:
        assert text in APPLICATIONS_INDEX_HTML

    css_path = (
        Path(__file__).resolve().parents[1]
        / "main_computer"
        / "web"
        / "applications"
        / "styles"
        / "email.css"
    )
    css = css_path.read_text(encoding="utf-8")
    assert "MCEL is the Email app layout layer" in css
    assert 'body[data-active-app="email"] #email-app[data-mcel-layout="email-app"]' in css
    assert 'body:not([data-active-app="email"]) #email-app[data-mcel-layout="email-app"]' in css
    assert '[data-mcel-layout="email-shell"]' in css
    assert '[data-mcel-layout="email-mail-workspace"]' in css
    assert '[data-mcel-layout-region="email-account-rail"]' in css
    assert '[data-mcel-layout-region="email-folder-nav"] button' in css
    assert '[data-mcel-layout-region="email-empty-state"]' in css
    assert 'grid-template-columns: minmax(240px, 280px) minmax(0, 1fr)' in css


def test_email_mailbox_css_uses_component_adaptive_rows_without_horizontal_spill() -> None:
    css_path = (
        Path(__file__).resolve().parents[1]
        / "main_computer"
        / "web"
        / "applications"
        / "styles"
        / "email.css"
    )
    css = css_path.read_text(encoding="utf-8")

    assert "container-name: email-mail-main" in css
    assert "@container email-mail-main" in css
    assert "overflow-x: hidden" in css
    assert "display: block;\n      inline-size: 100%;" in css
    assert "grid-column: 1 / -1;" in css
    assert "grid-row: 1 / -1;" in css
    assert "fit-content(5.25rem)" in css
    assert "minmax(16rem, 1fr)" not in css


def test_email_thread_reader_is_compact_and_does_not_stretch_system_labels() -> None:
    css_path = (
        Path(__file__).resolve().parents[1]
        / "main_computer"
        / "web"
        / "applications"
        / "styles"
        / "email.css"
    )
    css = css_path.read_text(encoding="utf-8")

    assert ".email-thread-card {\n      display: flex;" in css
    assert "justify-content: flex-start;" in css
    assert "min-height: 12rem" not in css
    assert "width: min(78ch, 100%);" in css
    assert "background: rgba(7, 8, 6, 0.38);" in css
    assert ".email-label-row {\n      display: flex;" in css
    assert "align-items: flex-start;" in css
    assert "flex: 0 0 auto;" in css
    assert "white-space: nowrap;" in css


def test_email_config_forms_are_compact_cards_not_stretched_panels() -> None:
    css_path = (
        Path(__file__).resolve().parents[1]
        / "main_computer"
        / "web"
        / "applications"
        / "styles"
        / "email.css"
    )
    css = css_path.read_text(encoding="utf-8")

    assert "Email config panes are forms, not presentation canvases" in css
    assert ".email-config-card.email-config-subpanel" in css
    assert "grid-auto-rows: max-content;" in css
    assert "align-content: start;" in css
    assert "min-height: 2.45rem;" in css
    assert "height: auto;" in css
    assert "border-left: 3px solid rgba(246, 199, 91, 0.62);" in css


def test_email_components_have_near_standard_mc_widget_metadata() -> None:
    email_path = (
        Path(__file__).resolve().parents[1]
        / "main_computer"
        / "web"
        / "applications"
        / "apps"
        / "email.html"
    )
    parser = _EmailComponentParser()
    parser.feed(email_path.read_text(encoding="utf-8"))
    parser.close()

    assert parser.components

    for attrs in parser.components:
        component_id = str(attrs["data-mc-component-id"])
        expected_widget_id = "email." + component_id.removeprefix("email.").replace(".", "-")
        component_kind = attrs.get("data-mc-component-kind")
        component_label = attrs.get("data-mc-component-label")

        assert attrs.get("data-mc-widget-id") == expected_widget_id, component_id
        assert attrs.get("data-mc-widget-kind") == component_kind, component_id
        assert attrs.get("data-mc-widget-class") == component_kind, component_id
        assert attrs.get("data-mc-widget-label") == component_label, component_id


def test_email_script_avoids_dom_binding_identifier_collisions() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    dom_bindings_path = (
        repo_root
        / "main_computer"
        / "web"
        / "applications"
        / "scripts"
        / "dom-bindings"
        / "email.js"
    )
    email_script_path = (
        repo_root
        / "main_computer"
        / "web"
        / "applications"
        / "scripts"
        / "email.js"
    )

    dom_binding_names = set(
        re.findall(
            r"^\s*const\s+([A-Za-z_$][\w$]*)\s*=",
            dom_bindings_path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    email_function_names = set(
        re.findall(
            r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
            email_script_path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )

    assert not (dom_binding_names & email_function_names)
    email_script = email_script_path.read_text(encoding="utf-8")
    assert "function emailCheckConfiguredServerAccount" in email_script
    assert "window.emailCheckServerAccount = emailCheckConfiguredServerAccount" in email_script
    assert "emailGmail" not in email_script
    assert "emailYahoo" not in email_script


def test_email_backend_check_route_is_registered_and_provider_oauth_routes_are_removed() -> None:
    dispatch_path = Path(__file__).resolve().parents[1] / "main_computer" / "viewport_route_dispatch.py"
    routes_path = Path(__file__).resolve().parents[1] / "main_computer" / "viewport_routes_applications.py"
    dispatch_text = dispatch_path.read_text(encoding="utf-8")
    routes_text = routes_path.read_text(encoding="utf-8")

    assert '"/api/applications/email/check"' in dispatch_text
    assert "def _handle_email_check_mail" in routes_text
    assert "/api/applications/email/oauth" not in dispatch_text
    assert "/api/applications/email/gmail" not in dispatch_text
    assert "gmail_client" not in routes_text
    assert "_handle_email_gmail" not in routes_text


def test_gmail_oauth_client_module_is_inert_raw_patch_tombstone() -> None:
    gmail_client_path = Path(__file__).resolve().parents[1] / "main_computer" / "gmail_client.py"
    text = gmail_client_path.read_text(encoding="utf-8")
    assert "inert compatibility tombstone" in text
    assert "GOOGLE_AUTH_ENDPOINT" not in text
    assert "start_google_oauth" not in text
    assert "gmail.googleapis.com" not in text


def test_email_check_config_normalizes_without_echoing_password() -> None:
    config = normalize_email_check_config(
        {
            "protocol": "IMAP",
            "security": "SSL",
            "host": "imap.example.com",
            "port": "993",
            "username": "user@example.com",
            "password": "secret-app-password",
            "provider": "custom",
            "accountId": "custom-1",
        }
    )
    public = public_email_check_summary(config)

    assert config["protocol"] == "imap"
    assert config["host"] == "imap.example.com"
    assert config["port"] == 993
    assert config["password"] == "secret-app-password"
    assert "password" not in public


def test_email_check_config_rejects_urls_as_hosts() -> None:
    try:
        normalize_email_check_config(
            {
                "protocol": "imap",
                "security": "ssl",
                "host": "https://imap.example.com",
                "port": "993",
                "username": "user@example.com",
                "password": "secret",
            }
        )
    except EmailClientConfigError as exc:
        assert "not a URL" in str(exc)
    else:
        raise AssertionError("Expected invalid host to be rejected")
