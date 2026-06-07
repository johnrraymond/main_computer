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
        "Unified inbox",
        "Search your mail",
        "New message",
        "data-email-tab=\"mail\"",
        "data-email-tab=\"config\"",
        "id=\"email-list-view\"",
        "id=\"email-thread-view\"",
        "id=\"email-back-to-list\"",
        "id=\"email-compose-modal\"",
        "id=\"email-reply-form\"",
        "data-email-config-tab=\"raw\"",
        "id=\"email-gmail-account-list\"",
        "id=\"email-yahoo-account-list\"",
        "id=\"email-raw-account-list\"",
        "Add Gmail account",
        "Add Yahoo account",
        "Add raw account",
        "data-email-config-tab=\"gmail\"",
        "data-email-config-tab=\"yahoo\"",
        "Raw POP/IMAP accounts",
        "Outlook.com",
        "Microsoft 365 / Exchange Online",
        "iCloud Mail",
        "AOL Mail",
        "Fastmail",
        "Zoho Mail",
        "Proton Mail Bridge",
        "function initEmailApp()",
        "emailBuildOAuthPlan",
        "emailCheckServerAccount",
        "function emailRenderMailView()",
        "function emailSaveInlineReply",
        "function emailSetComposeModalOpen",
        "/api/applications/email/check",
        "imap.gmail.com",
        "outlook.office365.com",
        "imap.mail.me.com",
        "imap.fastmail.com",
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
        "https://api.login.yahoo.com/oauth2/request_auth",
        "https://api.login.yahoo.com/oauth2/get_token",
        "window.MCEL.compile",
        'data-mc-component-id="email.root"',
        'data-mc="app"',
        'class="email-shell app-widget mc-app-shell"',
        'class="email-client-frame mc-app-workspace"',
    ]
    for text in expected:
        assert text in APPLICATIONS_INDEX_HTML


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
    assert "Welcome to the Email app" in APPLICATIONS_INDEX_HTML
    assert "How Check mail works" in APPLICATIONS_INDEX_HTML
    assert 'const emailSeedAccounts = Object.freeze([]);' in APPLICATIONS_INDEX_HTML
    assert 'main-computer-email-app-v5' in APPLICATIONS_INDEX_HTML

    assert 'id="email-list-view"' in APPLICATIONS_INDEX_HTML
    assert 'id="email-thread-view"' in APPLICATIONS_INDEX_HTML
    assert 'hidden\n            data-mc="panel"' in APPLICATIONS_INDEX_HTML
    assert 'class="email-message-item ${active ? "active" : ""} ${unread ? "unread" : ""}"' in APPLICATIONS_INDEX_HTML
    assert 'class="email-row-sender"' in APPLICATIONS_INDEX_HTML
    assert 'class="email-row-subject"' in APPLICATIONS_INDEX_HTML
    assert 'class="email-row-date"' in APPLICATIONS_INDEX_HTML
    assert 'emailAppState.activeMailView = "thread";' in APPLICATIONS_INDEX_HTML
    assert 'function emailReturnToMessageList()' in APPLICATIONS_INDEX_HTML
    assert 'class="email-compose-modal"' in APPLICATIONS_INDEX_HTML
    assert 'function emailSaveInlineReply' in APPLICATIONS_INDEX_HTML
    assert 'date.toDateString() === now.toDateString()' in APPLICATIONS_INDEX_HTML
    assert 'date.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"})' in APPLICATIONS_INDEX_HTML

def test_email_config_is_split_into_gmail_yahoo_and_raw_account_buckets() -> None:
    assert 'data-email-config-tab="gmail"' in APPLICATIONS_INDEX_HTML
    assert 'data-email-config-tab="yahoo"' in APPLICATIONS_INDEX_HTML
    assert 'data-email-config-tab="raw"' in APPLICATIONS_INDEX_HTML
    assert 'data-email-config-tab="imap"' not in APPLICATIONS_INDEX_HTML
    assert 'id="email-gmail-account-list"' in APPLICATIONS_INDEX_HTML
    assert 'id="email-yahoo-account-list"' in APPLICATIONS_INDEX_HTML
    assert 'id="email-raw-account-list"' in APPLICATIONS_INDEX_HTML
    assert "function emailRenderConfigAccountLists" in APPLICATIONS_INDEX_HTML
    assert 'activeConfigTab: "gmail"' in APPLICATIONS_INDEX_HTML
    assert '["gmail", "yahoo", "raw"]' in APPLICATIONS_INDEX_HTML


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

    assert "grid-template-rows: auto auto minmax(0, 1fr);" in css
    assert "container-name: email-mail-main" in css
    assert "@container email-mail-main" in css
    assert "overflow-x: hidden" in css
    assert "display: block;\n      inline-size: 100%;" in css
    assert "grid-template-columns: minmax(0, 1fr);" in css
    assert "grid-template-rows: minmax(0, 1fr);" in css
    assert "grid-column: 1 / -1;" in css
    assert "grid-row: 1 / -1;" in css
    assert "fit-content(5.25rem)" in css
    assert "minmax(0, 1fr)" in css
    assert "minmax(16rem, 1fr)" not in css
    assert "@container email-tab-panel (max-width: 760px)" in css
    assert ".email-config-account-list" in css
    assert "grid-template-columns: minmax(170px, 220px) minmax(0, 1fr);" in css


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
    assert "width: min(980px, 100%);" in css
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

def test_email_backend_check_route_is_registered() -> None:
    dispatch_path = Path(__file__).resolve().parents[1] / "main_computer" / "viewport_route_dispatch.py"
    routes_path = Path(__file__).resolve().parents[1] / "main_computer" / "viewport_routes_applications.py"

    assert '"/api/applications/email/check"' in dispatch_path.read_text(encoding="utf-8")
    assert "def _handle_email_check_mail" in routes_path.read_text(encoding="utf-8")


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
