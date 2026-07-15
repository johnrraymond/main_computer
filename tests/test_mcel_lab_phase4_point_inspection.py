from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_APP = ROOT / "main_computer" / "web" / "applications"
MCEL_LAB_HTML = WEB_APP / "apps" / "mcel-lab.html"
MCEL_LAB_JS = WEB_APP / "scripts" / "mcel-lab.js"
MCEL_LAB_CSS = WEB_APP / "styles" / "mcel-lab.css"
BLUEPRINTS = WEB_APP / "scripts" / "mcel-app-blueprints-core.js"
NAVIGATION = WEB_APP / "scripts" / "dom-bindings" / "navigation.js"


REQUIRED_SELECTED_ELEMENT_FIELDS = [
    "recordId",
    "appId",
    "owningAppId",
    "selectedAppId",
    "inspectionRootId",
    "inspectionContext",
    "selector",
    "previewPath",
    "visibleText",
    "tagName",
    "role",
    "mcelElementGuess",
    "layoutZone",
    "parentRegion",
    "boundingBox",
    "dataMcelAttributes",
    "nearbyElements",
    "sourceHints",
    "cssOwners",
    "jsOwners",
    "testHints",
]


def run_node_json(script: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_phase_four_blueprints_share_generic_point_inspection_policy() -> None:
    script = textwrap.dedent(
        """
        const fs = require("fs");
        global.window = global;
        eval(fs.readFileSync("main_computer/web/applications/scripts/mcel-app-blueprints-core.js", "utf8"));
        const doc = McelAppBlueprintsCore.inspectableBlueprintFor("document-editor");
        const lab = McelAppBlueprintsCore.inspectableBlueprintFor("mcel-lab");
        console.log(JSON.stringify({
          doc: doc.inspectionPolicy,
          lab: lab.inspectionPolicy,
          required: McelAppBlueprintsCore.requiredInspectionFields()
        }));
        """
    )
    result = run_node_json(script)

    for policy in (result["doc"], result["lab"]):
        assert policy["mode"] == "multi-surface-point-inspection"
        assert policy["enabled"] is True
        assert policy["patternId"] == "pattern.point-and-annotate"
        assert policy["preventDefaultActions"] is True
        assert policy["sourceMutationAllowed"] is False
        assert policy["hoverHighlight"] is True
        assert policy["selectionHighlight"] is True
        assert policy["coordinateHitTesting"] is True
        assert policy["proximityRadius"] >= 12
        assert policy["cycleModifier"] == "Alt"
        assert policy["revealSelectedElement"] is True
        assert policy["layoutZoneAttributes"] == [
            "data-mcel-layout-zone",
            "data-mcel-zone",
        ]
        for field in REQUIRED_SELECTED_ELEMENT_FIELDS:
            assert field in policy["requiredSelectedElementFields"]

    assert result["doc"]["inspectionRoots"][0] == {
        "id": "mounted-app",
        "context": "mounted-app",
        "selector": "[data-mcel-preview-clone]",
        "eventRootSelector": ".mcel-lab-mounted-preview-frame",
        "owningAppId": "document-editor",
        "excludeSelectors": [],
    }
    assert result["doc"]["inspectionRoots"][1] == {
        "id": "host-surface-1",
        "context": "host-app",
        "selector": "#mcel-lab-app [data-mcel-layout-zone='primary']",
        "eventRootSelector": "#mcel-lab-app [data-mcel-layout-zone='primary']",
        "owningAppId": "mcel-lab",
        "excludeSelectors": [".mcel-lab-mounted-preview-frame"],
    }
    assert result["lab"]["inspectionRoots"][0]["context"] == "mounted-app"
    assert result["lab"]["inspectionRoots"][1] == {
        "id": "host-surface-1",
        "context": "host-app",
        "selector": "#mcel-lab-app [data-mcel-layout-zone='primary']",
        "eventRootSelector": "#mcel-lab-app [data-mcel-layout-zone='primary']",
        "owningAppId": "mcel-lab",
        "excludeSelectors": [".mcel-lab-mounted-preview-frame"],
    }

    assert result["doc"]["selectorAttributes"] == result["lab"]["selectorAttributes"]
    assert result["doc"]["roleAttributes"] == result["lab"]["roleAttributes"]
    assert result["required"] == REQUIRED_SELECTED_ELEMENT_FIELDS


def test_phase_four_shell_exposes_real_inspect_action_and_selected_element_surface() -> None:
    source = MCEL_LAB_HTML.read_text(encoding="utf-8")
    primary_start = source.index('class="mcel-lab-blueprint-primary mc-app-primary"')
    primary_end = source.index("</main>", primary_start)
    right_start = source.index('class="mcel-lab-blueprint-right-rail"')
    advanced_start = source.index("Advanced / Legacy proof lab")
    primary = source[primary_start:primary_end]
    right = source[right_start:advanced_start]

    assert 'id="mcel-blueprint-inspect-action"' in source
    assert 'data-mcel-inspect-action="point-and-inspect"' in source
    assert 'aria-pressed="false"' in source
    assert 'id="mcel-blueprint-selection-status"' in source
    assert 'id="mcel-blueprint-inspect-mode-status"' in right
    assert 'id="mcel-blueprint-selected-element"' in right
    assert "Enable Inspect, then click an element" in source
    assert "Point-and-inspect will populate this panel" not in source
    assert "deferred to Phase 4" not in source
    assert 'class="mcel-lab-work-context"' in primary
    assert "<details class=\"mcel-lab-shell-card mcel-lab-navigation-disclosure\"" in source
    assert "<details open" not in right


def test_phase_four_script_selects_preview_elements_and_blocks_app_actions() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    phase_start = source.index("function mcelBlueprintShellInspectionPolicy")
    phase_end = source.index("function mcelBlueprintShellSanitizeToken", phase_start)
    phase = source[phase_start:phase_end]

    required_functions = [
        "function mcelBlueprintShellStableSelectorForNode",
        "function mcelBlueprintShellPreviewPathForNode",
        "function mcelBlueprintShellBuildSelectedElementRecord",
        "function mcelBlueprintShellLayoutZoneForNode",
        "function mcelBlueprintShellParentRegionForNode",
        "function mcelBlueprintShellNearbyElementsForNode",
        "function mcelBlueprintShellInspectionSurfaces",
        "function mcelBlueprintShellInspectionSurfaceForRecord",
        "function mcelBlueprintShellInspectionCandidateEntries",
        "function mcelBlueprintShellInspectionTargetFromEvent",
        "function mcelBlueprintShellRevealSelectedNode",
        "function mcelBlueprintShellBindInspectionSurface",
        "function mcelBlueprintShellBindInspectionFrame",
        "function mcelBlueprintShellBindInspectionSurfaces",
        "function mcelBlueprintShellSelectPreviewElement",
        "function mcelBlueprintShellApplyInspectMode",
        "function mcelBlueprintShellToggleInspectMode",
    ]
    for function_name in required_functions:
        assert function_name in phase

    assert 'eventRoot.addEventListener("pointerover"' in phase
    assert 'eventRoot.addEventListener("pointermove"' in phase
    assert 'eventRoot.addEventListener("pointerdown"' in phase
    assert "elementsFromPoint" in phase
    assert "geometry-overlap" in phase
    assert "geometry-proximity" in phase
    assert "event.altKey" in phase
    assert '"click", "dblclick", "contextmenu", "submit", "change", "input"' in phase
    assert "event.preventDefault()" in phase
    assert "event.stopImmediatePropagation()" in phase
    assert 'previewRoot.removeAttribute("inert")' in phase
    assert 'previewRoot.setAttribute("inert", "")' in phase
    assert "selectedElements" in source
    assert "window.McelLabPointInspect" in source

    assert "localStorage.setItem" not in phase
    point_inspection_core = phase.split(
        "function mcelBlueprintShellAnnotationPolicy",
        1,
    )[0]
    assert "fetch(" not in point_inspection_core
    assert "sourceMutationAllowed: false" in phase


def test_phase_four_preview_clone_strips_inline_handlers_without_disabling_controls() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    start = source.index("function mcelBlueprintShellPreparePreviewClone")
    end = source.index("function mcelBlueprintShellBuildMountReport", start)
    clone_block = source[start:end]

    assert 'if (/^on/i.test(name)) node.removeAttribute(name);' in clone_block
    assert 'node.setAttribute("tabindex", "-1")' in clone_block
    assert 'node.removeAttribute("autofocus")' in clone_block
    assert 'node.setAttribute("disabled", "")' not in clone_block


def test_phase_four_styles_make_preview_clickable_only_in_inspect_mode() -> None:
    source = MCEL_LAB_CSS.read_text(encoding="utf-8")

    assert ".mcel-lab-mounted-preview-frame [data-mcel-preview-clone]" in source
    assert "pointer-events: none" in source
    assert ".mcel-lab-mounted-preview-frame.is-inspecting [data-mcel-preview-clone]" in source
    assert "pointer-events: auto" in source
    assert ".mcel-preview-inspect-hover" in source
    assert ".mcel-preview-inspect-selected" in source
    assert ".mcel-preview-inspect-owner" in source
    assert ".mcel-lab-selected-element-facts" in source
    assert ".mcel-lab-rail-disclosure" in source
    assert "min-height: 560px" in source
    assert "max-height: 74vh" in source


def test_phase_four_removes_stale_compiler_chrome_language() -> None:
    source = NAVIGATION.read_text(encoding="utf-8")

    assert "Semantic HTML compiler workbench is ready." not in source
    assert "App blueprint and point-inspection workbench is ready." in source


def test_phase_four_does_not_pollute_product_apps_with_inspector_ui() -> None:
    for name in ("document.html", "git-tools.html", "code-editor.html"):
        source = (WEB_APP / "apps" / name).read_text(encoding="utf-8")
        assert "mcel-blueprint-inspect-action" not in source
        assert "mcel-preview-inspect-selected" not in source
        assert "McelLabPointInspect" not in source


def _extract_js_function(source: str, function_name: str) -> str:
    marker = f"function {function_name}"
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        character = source[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"Unclosed JavaScript function: {function_name}")


def test_phase_four_inspect_action_rebinds_the_real_button_without_a_sentinel() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    bind_start = source.index("function bindMcelBlueprintShellActionControls")
    bind_end = source.index("function renderMcelElementLibraryAcidTest", bind_start)
    binding = source[bind_start:bind_end]

    assert "function mcelBlueprintShellSetInspectMode" in source
    assert "function mcelBlueprintShellHandleInspectAction" in source
    assert "function mcelBlueprintShellBindInspectAction" in source
    assert "mcelBlueprintShellBindInspectAction(inspectButton)" in binding
    assert 'inspectButton.dataset.mcelBlueprintInspectBound = "direct-rebind-v4"' in source
    assert "inspectButton.__mcelBlueprintInspectHandler" in source
    assert 'inspectButton.removeEventListener("click", previousHandler, false)' in source
    assert 'inspectButton.addEventListener("click", nextHandler, false)' in source
    assert '__mcelBlueprintInspectWindowBinding !== "window-capture-v3"' not in source
    assert 'window.addEventListener("click", mcelBlueprintShellHandleWindowInspectAction, true)' not in source
    assert "event.stopImmediatePropagation()" in source
    assert "activate: () => mcelBlueprintShellSetInspectMode(true)" in source
    assert "deactivate: () => mcelBlueprintShellSetInspectMode(false)" in source

    render_start = source.index("function renderMcelBlueprintShell")
    render_end = source.index("function validateMcelBlueprintShell", render_start)
    render_block = source[render_start:render_end]
    assert "bindMcelBlueprintShellActionControls();" in render_block


def test_phase_four_inspect_rebind_replaces_a_stale_handler_and_invokes_once() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    bind_function = _extract_js_function(source, "mcelBlueprintShellBindInspectAction")
    script = textwrap.dedent(
        f"""
        global.window = {{}};
        global.document = {{
          getElementById() {{ return null; }}
        }};
        let inspectCalls = 0;
        function mcelBlueprintShellHandleInspectAction(event) {{
          inspectCalls += 1;
          return true;
        }}
        {bind_function}

        class FakeButton {{
          constructor() {{
            this.dataset = {{}};
            this.listeners = new Set();
            this.__mcelBlueprintInspectHandler = null;
          }}
          addEventListener(type, handler) {{
            if (type === "click") this.listeners.add(handler);
          }}
          removeEventListener(type, handler) {{
            if (type === "click") this.listeners.delete(handler);
          }}
          click() {{
            const event = {{
              currentTarget: this,
              preventDefault() {{}},
              stopImmediatePropagation() {{}}
            }};
            for (const handler of [...this.listeners]) handler(event);
          }}
        }}

        const button = new FakeButton();
        const stale = () => {{ inspectCalls += 100; }};
        button.listeners.add(stale);
        button.__mcelBlueprintInspectHandler = stale;

        mcelBlueprintShellBindInspectAction(button);
        mcelBlueprintShellBindInspectAction(button);
        button.click();

        console.log(JSON.stringify({{
          listenerCount: button.listeners.size,
          inspectCalls,
          marker: button.dataset.mcelBlueprintInspectBound,
          globalMarker: window.__mcelBlueprintInspectWindowBinding
        }}));
        """
    )
    result = run_node_json(script)

    assert result == {
        "listenerCount": 1,
        "inspectCalls": 1,
        "marker": "direct-rebind-v4",
        "globalMarker": "retired-direct-rebind-v4",
    }



def test_phase_four_inspection_state_survives_nested_state_reads() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    state_function = _extract_js_function(source, "mcelBlueprintShellState")
    script = textwrap.dedent(
        f"""
        global.mcelLabState = {{
          blueprintShell: {{
            appId: "document-editor",
            aspectId: "overview",
            mountedAppId: "document-editor",
            mountReports: {{"document-editor": {{route: "/applications/document"}}}},
            inspectionMode: false,
            selectedElements: {{}}
          }}
        }};

        {state_function}

        const first = mcelBlueprintShellState();
        const mountReports = first.mountReports;
        const selectedElements = first.selectedElements;

        // Reproduce the activation path: a nested state read occurs before the
        // caller mutates the state reference it already holds.
        mcelBlueprintShellState();
        first.inspectionMode = true;
        const afterNestedRead = mcelBlueprintShellState();

        console.log(JSON.stringify({{
          sameStateObject: first === afterNestedRead,
          sameMountReports: mountReports === afterNestedRead.mountReports,
          sameSelectedElements: selectedElements === afterNestedRead.selectedElements,
          inspectionMode: afterNestedRead.inspectionMode,
          reportPreserved: Boolean(afterNestedRead.mountReports["document-editor"])
        }}));
        """
    )
    result = run_node_json(script)

    assert result == {
        "sameStateObject": True,
        "sameMountReports": True,
        "sameSelectedElements": True,
        "inspectionMode": True,
        "reportPreserved": True,
    }

    assert 'stateModelVersion: "in-place-v5"' in source

def test_phase_four_app_switch_and_remount_leave_inspection_inert() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")

    mount_start = source.index("function mcelBlueprintShellMountSelectedApp")
    mount_end = source.index("function mcelBlueprintShellSelectApp", mount_start)
    mount_block = source[mount_start:mount_end]

    select_start = mount_end
    select_end = source.index("function renderMcelBlueprintShell", select_start)
    select_block = source[select_start:select_end]

    assert "shellState.inspectionMode = false;" in mount_block
    assert "shellState.inspectionMode = false;" in select_block


def test_phase_four_selection_output_is_visible_and_drills_into_the_inspector() -> None:
    html = MCEL_LAB_HTML.read_text(encoding="utf-8")
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    styles = MCEL_LAB_CSS.read_text(encoding="utf-8")

    assert 'id="mcel-blueprint-selection-receipt"' in html
    assert 'data-mcel-selection-state="empty"' in html
    assert 'id="mcel-blueprint-view-selection-action"' in html
    assert 'id="mcel-blueprint-selected-element-card"' in html
    assert 'aria-live="polite"' in html

    assert "function mcelBlueprintShellRenderSelectionReceipt" in source
    assert "function mcelBlueprintShellRenderSelectedElementFallback" in source
    assert 'selectionOutputVersion: "visible-receipt-v6"' in source
    assert 'new CustomEvent("mcel:element-selected"' in source
    assert "inspectorRendered" in source
    assert 'container.dataset.mcelSelectionRender = "complete"' in source
    assert 'container.dataset.mcelSelectionRender = "fallback"' in source
    assert 'card?.scrollIntoView?.({block: "nearest", inline: "nearest", behavior: "smooth"})' in source

    select_start = source.index("function mcelBlueprintShellSelectPreviewElement")
    select_end = source.index("function mcelBlueprintShellBindInspectionFrame", select_start)
    select_block = source[select_start:select_end]
    assert "shellState.selectedElements[blueprint.appId] = record;" in select_block
    assert "mcelBlueprintShellRenderSelectedElement(blueprint)" in select_block
    assert "mcelBlueprintShellRenderSelectionReceipt(blueprint)" in select_block

    assert ".mcel-lab-selection-receipt" in styles
    assert '.mcel-lab-selection-receipt[data-mcel-selection-state="selected"]' in styles
    assert '.mcel-lab-selected-element-card[data-mcel-selection-state="selected"]' in styles


def test_phase_four_coordinate_hit_testing_handles_scrollbar_obscured_targets() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    candidate_function = _extract_js_function(
        source,
        "mcelBlueprintShellInspectionCandidateEntries",
    )
    distance_function = _extract_js_function(
        source,
        "mcelBlueprintShellInspectionRectDistance",
    )
    point_function = _extract_js_function(
        source,
        "mcelBlueprintShellInspectionPointFromEvent",
    )
    depth_function = _extract_js_function(
        source,
        "mcelBlueprintShellInspectionNodeDepth",
    )
    semantic_function = _extract_js_function(
        source,
        "mcelBlueprintShellInspectionSemanticWeight",
    )
    eligible_function = _extract_js_function(
        source,
        "mcelBlueprintShellInspectionEligibleNode",
    )

    script = textwrap.dedent(
        f"""
        global.window = {{
          getComputedStyle() {{
            return {{display: "block", visibility: "visible", opacity: "1"}};
          }}
        }};

        function node(tag, rect, attrs = {{}}, parent = null) {{
          const value = {{
            nodeType: 1,
            tagName: tag.toUpperCase(),
            parentElement: parent,
            children: [],
            ownerDocument: null,
            getBoundingClientRect() {{ return rect; }},
            getAttribute(name) {{ return attrs[name] || null; }},
            hasAttribute(name) {{ return Object.prototype.hasOwnProperty.call(attrs, name); }},
            contains(candidate) {{
              let current = candidate;
              while (current) {{
                if (current === this) return true;
                current = current.parentElement;
              }}
              return false;
            }},
            querySelectorAll() {{
              const found = [];
              const visit = (current) => {{
                for (const child of current.children) {{
                  found.push(child);
                  visit(child);
                }}
              }};
              visit(this);
              return found;
            }}
          }};
          if (parent) parent.children.push(value);
          return value;
        }}

        const root = node("main", {{left: 0, top: 0, right: 300, bottom: 200, width: 300, height: 200}});
        const broad = node("div", {{left: 0, top: 0, right: 300, bottom: 200, width: 300, height: 200}}, {{}}, root);
        const yellowButton = node(
          "button",
          {{left: 250, top: 160, right: 295, bottom: 195, width: 45, height: 35}},
          {{"aria-label": "Yellow action"}},
          broad
        );

        const ownerDocument = {{
          // Native scrollbar chrome wins the browser hit test, so the app target
          // is absent from the direct point stack.
          elementsFromPoint() {{ return []; }}
        }};
        root.ownerDocument = ownerDocument;
        broad.ownerDocument = ownerDocument;
        yellowButton.ownerDocument = ownerDocument;

        const frame = {{
          querySelector(selector) {{
            return selector === "[data-mcel-preview-clone]" ? root : null;
          }}
        }};
        const event = {{
          clientX: 290,
          clientY: 190,
          altKey: false,
          composedPath() {{ return [frame]; }}
        }};

        function mcelBlueprintShellInspectionPolicy() {{
          return {{
            coordinateHitTesting: true,
            proximityRadius: 18
          }};
        }}

        {point_function}
        {distance_function}
        {depth_function}
        {semantic_function}
        {eligible_function}
        {candidate_function}

        const result = mcelBlueprintShellInspectionCandidateEntries(
          frame,
          event,
          {{appId: "document-editor"}}
        );

        console.log(JSON.stringify({{
          count: result.entries.length,
          firstTag: result.entries[0]?.node?.tagName || null,
          firstMode: result.entries[0]?.hitMode || null,
          includesYellow: result.entries.some((entry) => entry.node === yellowButton)
        }}));
        """
    )
    result = run_node_json(script)

    assert result["count"] >= 2
    assert result["firstTag"] == "BUTTON"
    assert result["firstMode"] == "geometry-overlap"
    assert result["includesYellow"] is True


def test_phase_four_overlap_selection_is_hint_driven_and_visible() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    blueprint_source = BLUEPRINTS.read_text(encoding="utf-8")

    assert 'hitTestingVersion: "multi-surface-v8"' in source
    assert 'inspectionRootsVersion: "host-primary-v8"' in source
    assert "record.hitTest = hitTest" in source
    assert "Candidate ${record.hitTest.candidateIndex + 1}" in source
    assert "mcelBlueprintShellRevealSelectedNode(target, previewRoot, frame, blueprint)" in source
    assert "Alt-click cycles overlapping or scrollbar-obscured candidates" not in source
    assert "cycleModifier" in source
    assert "scrollbar-obscured candidates" in source

    assert "coordinateHitTesting: true" in blueprint_source
    assert 'cycleModifier: "Alt"' in blueprint_source
    assert "revealSelectedElement: true" in blueprint_source


def test_phase_four_self_hosting_primary_surface_is_inspectable_without_hardcoded_receipt_logic() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    blueprint_source = BLUEPRINTS.read_text(encoding="utf-8")

    assert "hostSurfaceSelectors" in blueprint_source
    assert '"#mcel-lab-app [data-mcel-layout-zone=\'primary\']"' in blueprint_source
    assert '"#mcel-blueprint-selection-receipt"' not in blueprint_source
    assert "function mcelBlueprintShellBindInspectionSurface" in source
    assert 'const bindingKey = `${blueprint?.appId || "app"}:${surface.id}`;' in source
    assert "mcelBlueprintShellInspectionEventExcluded(event, surface)" in source
    assert "inspectionSurface: surface" in source
    assert "record.inspectionContext === \"host-app\"" in source
    assert "record.owningAppId || blueprint.appId" in source
    assert "function mcelBlueprintShellAnnotationBlueprintForRecord" in source
    assert "body: JSON.stringify({appId: annotationBlueprint.appId, annotation})" in source
    assert "appId: annotationBlueprint.appId" in source


def test_phase_four_host_surface_candidate_resolution_can_select_the_selection_receipt() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    candidate_function = _extract_js_function(
        source,
        "mcelBlueprintShellInspectionCandidateEntries",
    )
    distance_function = _extract_js_function(
        source,
        "mcelBlueprintShellInspectionRectDistance",
    )
    point_function = _extract_js_function(
        source,
        "mcelBlueprintShellInspectionPointFromEvent",
    )
    depth_function = _extract_js_function(
        source,
        "mcelBlueprintShellInspectionNodeDepth",
    )
    semantic_function = _extract_js_function(
        source,
        "mcelBlueprintShellInspectionSemanticWeight",
    )
    eligible_function = _extract_js_function(
        source,
        "mcelBlueprintShellInspectionEligibleNode",
    )

    script = textwrap.dedent(
        f"""
        global.window = {{
          getComputedStyle() {{
            return {{display: "block", visibility: "visible", opacity: "1"}};
          }}
        }};

        function node(tag, rect, attrs = {{}}, parent = null) {{
          const value = {{
            nodeType: 1,
            tagName: tag.toUpperCase(),
            parentElement: parent,
            children: [],
            ownerDocument: null,
            getBoundingClientRect() {{ return rect; }},
            getAttribute(name) {{ return attrs[name] || null; }},
            hasAttribute(name) {{ return Object.prototype.hasOwnProperty.call(attrs, name); }},
            contains(candidate) {{
              let current = candidate;
              while (current) {{
                if (current === this) return true;
                current = current.parentElement;
              }}
              return false;
            }},
            querySelectorAll() {{
              const found = [];
              const visit = (current) => {{
                for (const child of current.children) {{
                  found.push(child);
                  visit(child);
                }}
              }};
              visit(this);
              return found;
            }}
          }};
          if (parent) parent.children.push(value);
          return value;
        }}

        const hostPrimary = node(
          "main",
          {{left: 0, top: 0, right: 500, bottom: 500, width: 500, height: 500}},
          {{"data-mcel-layout-zone": "primary"}}
        );
        const receipt = node(
          "div",
          {{left: 20, top: 450, right: 480, bottom: 490, width: 460, height: 40}},
          {{id: "mcel-blueprint-selection-receipt", "aria-label": "Selection receipt"}},
          hostPrimary
        );
        const ownerDocument = {{
          elementsFromPoint() {{ return [receipt, hostPrimary]; }}
        }};
        hostPrimary.ownerDocument = ownerDocument;
        receipt.ownerDocument = ownerDocument;

        const eventRoot = hostPrimary;
        const event = {{
          clientX: 100,
          clientY: 470,
          altKey: false,
          type: "pointerdown",
          target: receipt,
          composedPath() {{ return [receipt, hostPrimary]; }}
        }};
        const surface = {{
          id: "host-surface-1",
          context: "host-app",
          owningAppId: "mcel-lab",
          root: hostPrimary,
          eventRoot
        }};

        function mcelBlueprintShellInspectionPolicy() {{
          return {{
            coordinateHitTesting: true,
            proximityRadius: 18
          }};
        }}

        {point_function}
        {distance_function}
        {depth_function}
        {semantic_function}
        {eligible_function}
        {candidate_function}

        const result = mcelBlueprintShellInspectionCandidateEntries(
          eventRoot,
          event,
          {{appId: "mcel-lab"}},
          surface
        );

        console.log(JSON.stringify({{
          count: result.entries.length,
          firstIsReceipt: result.entries[0]?.node === receipt,
          firstMode: result.entries[0]?.hitMode || null
        }}));
        """
    )
    result = run_node_json(script)

    assert result["count"] >= 2
    assert result["firstIsReceipt"] is True
    assert result["firstMode"] == "coordinate-stack"
