from __future__ import annotations

import json
import re

from pathlib import Path

from main_computer.mcel_application_packages import build_application_package_catalog


REPO = Path(__file__).resolve().parents[3]


def _code_editor_package_record():
    catalog = build_application_package_catalog(REPO)
    assert catalog.ok is True
    return next(item for item in catalog.packages if item.app_id == "code-editor")


def _code_editor_normalized_ir() -> dict:
    record = _code_editor_package_record()
    raw = record.files["generated/mcel.application.normalized.json"].decode("utf-8")
    return json.loads(raw)


def _selector_part_exists(selector: str, html: str) -> bool:
    selector = selector.strip()
    if not selector:
        return False
    if selector.startswith("#") and re.fullmatch(r"#[A-Za-z0-9_-]+", selector):
        return f'id="{selector[1:]}"' in html or f"id='{selector[1:]}'" in html
    if selector.startswith(".") and re.fullmatch(r"\.[A-Za-z0-9_-]+", selector):
        class_name = re.escape(selector[1:])
        return re.search(r'class=(?:"[^"]*\b' + class_name + r'\b[^"]*"|\'[^\']*\b' + class_name + r'\b[^\']*\')', html) is not None
    attr_match = re.fullmatch(r"\[([A-Za-z0-9_-]+)(?:=(?:'([^']*)'|\"([^\"]*)\"))?\]", selector)
    if attr_match:
        attr = attr_match.group(1)
        value = attr_match.group(2) if attr_match.group(2) is not None else attr_match.group(3)
        if value is None:
            return attr in html
        return f'{attr}="{value}"' in html or f"{attr}='{value}'" in html
    return selector in html


def _selector_exists(selector: str, html: str) -> bool:
    return any(_selector_part_exists(part, html) for part in selector.split(","))



def test_code_editor_existing_host_surface_remains_present_for_host_bound_projection() -> None:
    html = (REPO / "main_computer/web/applications/apps/code-editor.html").read_text(encoding="utf-8")
    shell = (REPO / "main_computer/web/applications.html").read_text(encoding="utf-8")

    assert 'id="code-editor-app"' in html
    assert "<!-- @include applications/apps/code-editor.html -->" in shell
    assert "<!-- @include applications/scripts/code-editor-monaco-adapter.js -->" in shell
    assert "<!-- @include applications/scripts/code-editor-mcel-studio.js -->" in shell
    assert "<!-- @include applications/scripts/code-editor-core.js -->" in shell
    assert "<!-- @include applications/scripts/code-editor-view-model.js -->" in shell
    assert "<!-- @include applications/scripts/code-editor-capabilities.js -->" in shell
    assert "<!-- @include applications/scripts/code-editor.js -->" in shell
    assert "<!-- @include applications/scripts/code-editor-browser-smoke.js -->" in shell
    assert "<!-- @include applications/scripts/code-editor-semantic-adapter.js -->" not in shell
    assert shell.index("applications/scripts/code-editor-monaco-adapter.js") < shell.index("applications/scripts/code-editor.js")
    assert shell.index("applications/scripts/code-editor-mcel-studio.js") < shell.index("applications/scripts/code-editor.js")
    assert shell.index("applications/scripts/mcel-diagnostics-counter-widget.js") < shell.index("applications/scripts/code-editor-browser-smoke.js")


def test_code_editor_canonical_runtime_facade_is_declared_but_not_shimmed_to_old_studio() -> None:
    source = (REPO / "mcel_apps/code-editor/application.js").read_text(encoding="utf-8")
    assert 'runtimeFacade: "MainComputerCodeEditorRuntime"' in source
    assert "MainComputerCodeStudio" not in source


def test_code_editor_monaco_host_is_permanent_center_pane_surface() -> None:
    runtime = (REPO / "main_computer/web/applications/scripts/code-editor.js").read_text(encoding="utf-8")
    styles = (REPO / "main_computer/web/applications/styles/code-editor.css").read_text(encoding="utf-8")
    adapter = (REPO / "main_computer/web/applications/scripts/code-editor-monaco-adapter.js").read_text(encoding="utf-8")

    assert "monacoDocumentFor(active)" in runtime
    assert "__no-file-open__.md" in runtime
    assert "readOnly: true" in runtime
    assert "readOnly: !documentModel.placeholder" not in runtime
    assert "does not flip" in runtime
    assert "#code-editor-app[data-code-editor-runtime=\"dsl-native\"] #code-studio-runtime-monaco.code-studio-monaco-host.mcel-code-editor-authoring-host" in styles
    assert "#code-editor-app[data-code-editor-runtime-surface-mode=\"legacy-fidelity\"] .code-studio-body" in styles
    assert "Patch 11: legacy-fidelity grid-area repair" in styles
    assert "Patch 12: legacy-fidelity mode separates" in styles
    assert "Patch 14: legacy-fidelity shell direct-child containment" in styles
    assert "#code-editor-app[data-code-editor-runtime-surface-mode=\"legacy-fidelity\"] .code-studio-shell > *" in styles
    assert "grid-auto-columns: 0 !important;" in styles
    assert 'grid-template-areas: "activitybar sidebar editor inspector" !important;' in styles
    assert 'grid-template-areas: "activitybar sidebar editor" !important;' in styles
    assert "preserveExistingCodeStudioSurface(rootNode)" in runtime
    assert "activateLegacyRuntimePane" in runtime
    assert 'runtimePane.dataset.codeEditorRuntimePrimaryPane = "true"' in runtime
    assert "renderPrimaryEditorPreview(active, patch, receipts)" in runtime
    assert "parseAuthoredSourceWorkspace()" in runtime
    assert "bindLegacySourceFileClicks()" in runtime
    assert "#code-studio-source-editor is the authored source workspace, not a draft mirror." in runtime
    assert "box-sizing: content-box !important" in styles
    assert 'const LOCAL_VS_BASE = "/applications/vendor/monaco-editor/min/vs";' in adapter
    assert "activeSession.host" in adapter
    assert "window.__CE_MONACO_MODEL__ = model" in adapter
    assert "inspect," in adapter


def test_code_editor_browser_smoke_contract_is_loaded_and_checks_resize_regressions() -> None:
    shell = (REPO / "main_computer/web/applications.html").read_text(encoding="utf-8")
    smoke = (REPO / "main_computer/web/applications/scripts/code-editor-browser-smoke.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/code-editor-browser-smoke.js -->" in shell
    assert "code-editor.browser-smoke.v1" in smoke
    assert "MainComputerCodeEditorBrowserSmoke" in smoke
    assert "startResizeWatch" in smoke
    assert "shell-single-column-grid" in smoke
    assert "workbench-grid-nonzero-tracks" in smoke
    assert "runtime-pane-is-single-active-pane" in smoke
    assert "monaco-primary-surface-usable" in smoke
    assert "proof-dock-hidden-by-default" in smoke
    assert "diagnostics-raw-verdict-pass" in smoke


def test_code_editor_static_semantic_surface_is_declared_in_dsl_package() -> None:
    html = (REPO / "main_computer/web/applications/apps/code-editor.html").read_text(encoding="utf-8")
    runtime = (REPO / "main_computer/web/applications/scripts/code-editor-mcel-studio.js").read_text(encoding="utf-8")
    ir = _code_editor_normalized_ir()

    surface = next(item for item in ir["surfaces"] if item["id"] == "surface:code-editor.workspace")
    semantic = surface["semanticSurface"]

    assert semantic["id"] == "code-editor.semantic-surface.legacy-fidelity"
    assert semantic["surfaceId"] == "code-editor.surface.monaco-selected-file-editor"
    assert semantic["presentationAuthority"] == "existing-host-html"
    assert semantic["runtimeFacade"] == "MainComputerCodeEditorRuntime"

    region_ids = [region["id"] for region in semantic["regions"]]
    assert len(region_ids) == len(set(region_ids))
    assert {"activitybar", "explorer", "open-editors", "editor-group", "primary-editor", "assistant", "proof-dock"}.issubset(region_ids)

    primary = next(region for region in semantic["regions"] if region["id"] == "primary-editor")
    assert primary["primary"] is True
    assert primary["selector"] == "#code-studio-runtime-preview"
    assert primary["runtimeHostSelector"] == "#code-studio-runtime-monaco"
    assert 'id="code-studio-runtime-monaco"' in runtime

    for region in semantic["regions"]:
        assert _selector_exists(region["selector"], html), region

    intent_names = {item["sourceName"] for item in ir["intents"]}
    control_ids = [control["id"] for control in semantic["controls"]]
    assert len(control_ids) == len(set(control_ids))
    assert {"inspect-workspace", "open-source-file", "edit-draft", "save-file", "preview-aider-plan", "apply-reviewed-patch"}.issubset(control_ids)
    for control in semantic["controls"]:
        assert control["intent"] in intent_names
        assert _selector_exists(control["selector"], html), control
        if "runtimeHostSelector" in control:
            assert control["runtimeHostSelector"] in runtime

    forbidden = {item["id"]: item for item in semantic["forbiddenDefaultRegions"]}
    assert {"source-pane", "serialized-pane", "contract-pane", "proof-dock"} <= set(forbidden)
    assert all(item["defaultVisible"] is False for item in forbidden.values())
    for item in forbidden.values():
        assert _selector_exists(item["selector"], html), item


def test_code_editor_static_layout_grammar_is_declared_in_dsl_package() -> None:
    html = (REPO / "main_computer/web/applications/apps/code-editor.html").read_text(encoding="utf-8")
    runtime = (REPO / "main_computer/web/applications/scripts/code-editor-mcel-studio.js").read_text(encoding="utf-8")
    ir = _code_editor_normalized_ir()

    layout = next(item for item in ir["layouts"] if item["id"] == "layout:code-editor.workspace")
    grammar = layout["layoutGrammar"]

    assert grammar["id"] == "code-editor.layout.legacy-fidelity-workbench"
    assert grammar["rootSelector"] == ".code-studio-shell"

    region_ids = [region["id"] for region in grammar["regions"]]
    assert len(region_ids) == len(set(region_ids))
    assert {"shell", "titlebar", "workbench", "activitybar", "explorer", "editor-group", "primary-editor", "assistant", "proof", "statusbar"}.issubset(region_ids)
    assert next(region for region in grammar["regions"] if region["id"] == "shell")["children"] == ["titlebar", "workbench", "proof", "statusbar"]
    assert next(region for region in grammar["regions"] if region["id"] == "workbench")["children"] == ["activitybar", "explorer", "editor-group", "assistant"]

    for region in grammar["regions"]:
        assert _selector_exists(region["selector"], html), region
        if "runtimeHostSelector" in region:
            assert region["runtimeHostSelector"] in runtime

    constraints = {item["id"]: item for item in grammar["constraints"]}
    assert {
        "shell-single-column",
        "shell-direct-children-pinned",
        "workbench-nonzero-tracks",
        "primary-editor-nonzero",
        "proof-dock-hidden-by-default",
        "source-contract-panes-hidden-by-default",
    } <= set(constraints)
    assert constraints["primary-editor-nonzero"]["minWidth"] == 360
    assert constraints["primary-editor-nonzero"]["minHeight"] == {"compactViewport": 240, "default": 320}
    assert constraints["proof-dock-hidden-by-default"]["defaultVisible"] is False

    for constraint in constraints.values():
        assert _selector_exists(constraint["selector"], html), constraint
        if "runtimeHostSelector" in constraint:
            assert constraint["runtimeHostSelector"] in runtime



def test_code_editor_surface_bundle_is_materialized_from_static_declarations() -> None:
    record = _code_editor_package_record()
    ir = _code_editor_normalized_ir()
    surface = next(item for item in ir["surfaces"] if item["id"] == "surface:code-editor.workspace")
    layout = next(item for item in ir["layouts"] if item["id"] == "layout:code-editor.workspace")

    assert record.contracts["surfaceBundle"] == "mcel_apps/code-editor/contracts/surface-bundle.json"
    bundle = json.loads(record.files["contracts/surface-bundle.json"].decode("utf-8"))

    assert bundle["schema"] == "mcel.application-surface-bundle.v1"
    assert bundle["appId"] == "code-editor"
    assert bundle["surfaceId"] == "code-editor.surface.monaco-selected-file-editor"
    assert bundle["workspaceSurface"] == "surface:code-editor.workspace"
    assert bundle["contractId"] == "code-editor.contract.authoring.monaco-golden-path"
    assert bundle["route"] == "/applications/code-editor"
    assert bundle["rootSelector"] == "#code-editor-app"
    assert bundle["presentationAuthority"] == "existing-host-html"
    assert bundle["runtimeFacade"] == "MainComputerCodeEditorRuntime"
    assert bundle["semanticSurface"] == surface["semanticSurface"]
    assert bundle["layoutGrammar"] == layout["layoutGrammar"]
    assert bundle["semanticSurface"]["regions"]
    assert bundle["semanticSurface"]["controls"]
    assert bundle["layoutGrammar"]["regions"]
    assert bundle["layoutGrammar"]["constraints"]
