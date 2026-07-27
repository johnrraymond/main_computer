from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_HTML = ROOT / "main_computer" / "web" / "applications" / "apps" / "website-builder.html"
APP_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "website-builder.js"


def _javascript_function(source: str, name: str) -> str:
    match = re.search(rf"\b(?:async\s+)?function\s+{re.escape(name)}\s*\(", source)
    assert match, f"missing JavaScript function: {name}"
    paren = source.find("(", match.start())
    assert paren >= 0, f"missing parameter list for JavaScript function: {name}"
    paren_depth = 0
    quote = ""
    escaped = False
    index = paren
    while index < len(source):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {'"', "'", "`"}:
            quote = char
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth -= 1
            if paren_depth == 0:
                break
        index += 1
    opening = source.find("{", index + 1)
    assert opening >= 0, f"missing body for JavaScript function: {name}"

    depth = 0
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    index = opening
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and following == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char == "/" and following == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and following == "*":
            block_comment = True
            index += 2
            continue
        if char in {'"', "'", "`"}:
            quote = char
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[match.start(): index + 1]
        index += 1

    raise AssertionError(f"unterminated JavaScript function: {name}")


def test_website_project_model_exposes_saved_sites_and_artifacts() -> None:
    for site_id in ("hub-site", "johnrraymond"):
        site_root = ROOT / "runtime" / "websites" / site_id
        manifest = json.loads((site_root / "site.json").read_text(encoding="utf-8"))
        assert manifest["id"] == site_id
        for artifact_name in ("site.json", "builder.json", "index.html", "style.css", "script.js", "runtime.js"):
            assert (site_root / artifact_name).is_file(), f"{site_id} missing {artifact_name}"

    html = APP_HTML.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")
    assert "website-builder-site-select" in html
    assert "websiteBuilderSiteSelect" in script
    assert "websiteBuilderStateModel.selectedSiteId" in script
    assert "websiteBuilderStateModel.selectedSite" in script


def test_website_builder_save_preview_publish_actions_are_separate() -> None:
    script = APP_JS.read_text(encoding="utf-8")

    save = _javascript_function(script, "saveWebsiteBuilderSite")
    draft_preview = _javascript_function(script, "setWebsiteBuilderDraftPreview")
    published_preview = _javascript_function(script, "setWebsiteBuilderPublishedPreview")
    publish_payload = _javascript_function(script, "websiteBuilderPublishPayload")
    publish_api = _javascript_function(script, "websiteBuilderPublishApi")
    publish = _javascript_function(script, "publishWebsiteBuilderSite")

    assert '"/api/applications/websites/site/save"' in save
    assert "setWebsiteBuilderDraftPreview" in save
    assert "setWebsiteBuilderLog" in save
    assert "/site/publish" not in save
    assert "/site/git" not in save

    assert "websiteBuilderApi(" not in draft_preview
    assert "fetch(" not in draft_preview
    assert "srcdoc" in draft_preview
    assert "setWebsiteBuilderPreviewLabel" in draft_preview

    assert "websiteBuilderApi(" not in published_preview
    assert "fetch(" not in published_preview
    assert "setWebsiteBuilderPreviewLabel" in published_preview

    assert '"/api/applications/websites/site/publish"' in publish_api
    assert "lane" in publish_payload
    assert "websiteBuilderPublishApi" in publish
    assert "setWebsiteBuilderLog" in publish

    for body in (publish_payload, publish_api, publish):
        lowered = body.lower()
        assert "/site/git" not in lowered
        assert "git commit" not in lowered
        assert "git push" not in lowered
        assert "revision checkpoint" not in lowered

    assert 'websiteBuilderSave?.addEventListener("click"' in script
    assert 'websiteBuilderPreviewDraft?.addEventListener("click"' in script
    assert 'websiteBuilderPublishRemote?.addEventListener("click"' in script
    assert "websiteBuilderGitToggle" in script


def test_website_builder_publish_lanes_and_verification_are_separate() -> None:
    html = APP_HTML.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    for control_id in (
        "website-builder-publish-local",
        "website-builder-publish-dev",
        "website-builder-publish-remote",
        "website-builder-visit-local",
        "website-builder-visit-dev",
        "website-builder-visit-remote-prod",
    ):
        assert control_id in html

    lane_label = _javascript_function(script, "websiteBuilderLaneLabel")
    visit_buttons = _javascript_function(script, "updateWebsiteBuilderVisitButtons")
    publish_controls = _javascript_function(script, "updateWebsiteBuilderPublishActionControls")
    can_publish = _javascript_function(script, "websiteBuilderCanPublishAcceptedSetup")
    publish = _javascript_function(script, "publishWebsiteBuilderSite")

    assert '"remote_prod"' in lane_label
    assert '"dev"' in lane_label
    assert '"Publish"' in lane_label
    assert '"Deploy"' in lane_label
    assert '"Local Server"' in lane_label

    assert 'websiteBuilderVisitUrl(site, "local")' in visit_buttons
    assert 'websiteBuilderVisitUrl(site, "dev")' in visit_buttons
    assert 'websiteBuilderVisitUrl(site, "remote_prod")' in visit_buttons
    assert '"Local Server"' in visit_buttons
    assert '"Deploy"' in visit_buttons
    assert '"Publish"' in visit_buttons

    assert "websiteBuilderAcceptedPublishTarget" in can_publish
    assert "Accept a publishing setup before publishing." in publish_controls
    assert 'publishWebsiteBuilderSite("local")' in script
    assert 'publishWebsiteBuilderSite("dev")' in script
    assert 'publishWebsiteBuilderSite("remote_prod")' in script

    success_guard = publish.index("if (payload.ok)")
    remember_remote = publish.index("rememberWebsiteBuilderRemotePublishedUrl")
    set_preview = publish.index("setWebsiteBuilderPublishedPreview")
    assert success_guard < remember_remote
    assert success_guard < set_preview
