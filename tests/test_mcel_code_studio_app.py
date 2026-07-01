from __future__ import annotations

from pathlib import Path
import unittest

from main_computer.viewport import APPLICATIONS_INDEX_HTML


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "main_computer" / "web" / "applications" / "apps" / "code-editor.html"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
STYLE_PATH = ROOT / "main_computer" / "web" / "applications" / "styles" / "code-editor.css"
SCRIPT_PATH = ROOT / "main_computer" / "web" / "applications" / "scripts" / "code-editor-mcel-studio.js"
PRETTY_DOC = ROOT / "pretty_docs" / "mcel-code-studio-example.md"


class McelCodeStudioAppTests(unittest.TestCase):
    def test_code_editor_is_source_safe_workbench_not_page_stack(self) -> None:
        app = APP_PATH.read_text(encoding="utf-8")
        expected = [
            "MCEL Code Studio",
            "source-safe-code-editor",
            "code-studio-titlebar",
            "code-studio-activitybar",
            "code-studio-sidebar",
            "code-studio-editor-group",
            "code-studio-inspector",
            "code-studio-statusbar",
            'id="code-studio-source-editor"',
            'id="code-studio-runtime-preview"',
            'id="code-studio-serialized-output"',
            'id="code-studio-contract-report"',
            'id="code-studio-scm-evidence-panel"',
            'id="code-studio-refresh-scm-evidence"',
            'id="code-studio-damage-runtime"',
            'id="code-studio-repair-runtime"',
            'id="code-studio-bottom-panel" data-expanded="false"',
            'id="code-studio-toggle-assistant"',
            "Author-owned source",
            "Generated runtime",
            "Serialized clean source",
            "MCEL contract report",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, app)

        self.assertNotIn("<main class=\"code-studio-editor-group\"", app)
        self.assertNotIn("<header class=\"code-studio-titlebar\"", app)

    def test_layout_is_locked_to_a_workbench_viewport(self) -> None:
        style = STYLE_PATH.read_text(encoding="utf-8")
        expected = [
            ".code-editor-app {",
            "height: clamp(720px, calc(100dvh - 150px), 1040px);",
            "max-height: calc(100dvh - 112px);",
            ".code-studio-shell {",
            "grid-template-rows: 36px minmax(0, 1fr) 24px;",
            ".code-studio-body {",
            "grid-template-columns: 48px clamp(230px, 18vw, 300px) minmax(480px, 1fr) clamp(280px, 22vw, 360px);",
            ".code-studio-sidebar {",
            "grid-template-rows: auto auto minmax(0, 1fr);",
            ".code-studio-editor-group {",
            "grid-template-columns: none;",
            "padding: 0;",
            ".code-studio-bottom-panel[data-expanded=\"false\"] .code-studio-aider-shell",
            ".code-studio-titlebar button {",
            "color: #dcdcdc;",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, style)

    def test_existing_aider_file_map_docs_and_gridstack_hooks_remain_available(self) -> None:
        expected = [
            'data-mc-widget-id="code-editor.file-map-panel"',
            'data-mc-widget-id="code-editor.aider-workspace"',
            'id="file-map-refresh"',
            'id="file-map-apply"',
            'id="aider-instruction"',
            'id="aider-preview"',
            'id="aider-run"',
            'id="aider-output"',
            'id="aider-history-list"',
            'id="aider-archive-list"',
            'id="code-editor-doc-viewport"',
            'id="code-editor-doc-load"',
            'id="code-editor-gridstack-toggle"',
            'id="code-editor-gridstack-reset"',
            'id="code-editor-gridstack-status"',
            "code-editor-file-map.js",
            "code-editor-aider-actions.js",
            "code-editor-documentation-viewport.js",
            "window.MainComputerCodeStudio",
            "gridstack.min.css",
            "gridstack-all.js",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)

    def test_code_studio_live_script_loads_after_scm_manifest(self) -> None:
        include_order = [
            "applications/scripts/mcel-scm.js",
            "applications/scripts/mcel-core.js",
            "applications/scripts/code-editor-scm-manifest.js",
            "applications/scripts/code-editor-mcel-studio.js",
        ]
        applications_html = APPLICATIONS_HTML.read_text(encoding="utf-8")
        positions = [applications_html.index(include) for include in include_order]

        self.assertEqual(positions, sorted(positions))

    def test_code_studio_script_exposes_contract_workflow(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        expected = [
            "window.MainComputerCodeStudio",
            "validateSource",
            "renderRuntime",
            "damageRuntime",
            "repairRuntime",
            "serializeCleanSource",
            "commitRuntimeDraft",
            "data-mc-generated=\"runtime\"",
            "data-mc-serialize=\"omit\"",
            "source-safe-code-editor",
            "code-studio-toggle-assistant",
            "code-studio-bottom-panel",
            "syncScmInstance",
            "runScmRuntimeChecks",
            "SCM serialization gate passed",
            "mcel.serializeComponent",
            "mcel.repairComponent",
            "mcel.checkLayoutContract",
            "mcel.checkStyleContract",
            "mcel.runEffect",
            "mcel.enterRoute",
            "mcel.runRouteLoader",
            "mcel.leaveRoute",
            "exportScmEvidence",
            "exportScmRouteEvidence",
            "syncScmRouteInstance",
            "enterScmRouteAndRunLoaders",
            "requestScmRouteLeave",
            "collectScmEvidenceSummary",
            "renderScmEvidencePanel",
            "ensureCodeStudioScmSurfaceStyles",
            "function scopedNodes",
            "root.matches?.(selector)",
            "backgroundColor: \"#1e1e1e\"",
            "bottomDock.style.maxHeight = \"80px\"",
            "evidenceFilterMatches",
            "formatEvidenceDetail",
            "buildScmEvidenceDebugPacket",
            "exportScmEvidenceDebugPacket",
            "copyCurrentScmEvidenceDebugPacket",
            "downloadCurrentScmEvidenceDebugPacket",
            "replayScmEvidenceEntry",
            "code-studio-scm-evidence-entry",
            "code-studio-scm-evidence-detail",
            "code-studio-replay-scm-evidence",
            "Replay selected gate",
            "SCM evidence refreshed",
            "SCM route blocked navigation",
            "Layout locked",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, script)


    def test_code_studio_live_script_wires_structured_route_lifecycle(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        expected = [
            "let scmRouteInstance = null;",
            "function routeParamsForScm",
            "function routeQueryForScm",
            "function syncScmRouteInstance",
            "function enterScmRouteAndRunLoaders",
            "function requestScmRouteLeave",
            "function canNavigateScmRoute",
            'mcel.runRouteLoader(routeInstance, "loadWorkspace")',
            'mcel.runRouteLoader(routeInstance, "loadFile")',
            'mcel.runEffect(instance, "loadWorkspace"',
            'mcel.runEffect(instance, "loadFile"',
            'mcel.runEffect(instance, "saveFile"',
            "mcel.enterRoute(scmRouteInstance",
            "mcel.leaveRoute(routeInstance",
            "recentRouteEvidence",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, script)

        runtime_switch = script.index('button.dataset.codeStudioRuntimeFile || ""')
        runtime_leave = script.rfind("canNavigateScmRoute", 0, runtime_switch + 250)
        runtime_select = script.index("studioState.selectedPath = nextPath;", runtime_switch)
        self.assertLess(runtime_leave, runtime_select)

        explorer_switch = script.index("button.dataset.codeStudioFile || studioState.selectedPath")
        explorer_leave = script.rfind("canNavigateScmRoute", 0, explorer_switch + 250)
        explorer_select = script.index("studioState.selectedPath = nextPath;", explorer_switch)
        self.assertLess(explorer_leave, explorer_select)


    def test_code_studio_scm_evidence_panel_is_visible_and_refreshable(self) -> None:
        app = APP_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        expected_markup = [
            "SCM evidence timeline",
            'id="code-studio-scm-evidence-panel"',
            'id="code-studio-refresh-scm-evidence"',
            'id="code-studio-scm-evidence-filter"',
            'id="code-studio-replay-scm-evidence"',
            'id="code-studio-export-scm-evidence-packet"',
            'id="code-studio-download-scm-evidence-packet"',
            'id="code-studio-scm-evidence-detail"',
            "Replay selected gate",
            "Export SCM Evidence Packet",
            "Download packet",
            'data-mc-component-id="code-editor.studio.scm-evidence"',
        ]
        for text in expected_markup:
            with self.subTest(markup=text):
                self.assertIn(text, app)

        expected_style = [
            ".code-studio-scm-evidence {",
            ".code-studio-scm-evidence-summary",
            ".code-studio-scm-evidence-actions",
            ".code-studio-scm-evidence-drilldown",
            ".code-studio-scm-evidence-entry",
            ".code-studio-scm-evidence-entry[data-ok=\"false\"]",
            ".code-studio-scm-evidence-entry[data-selected=\"true\"]",
            ".code-studio-scm-evidence-detail",
        ]
        for text in expected_style:
            with self.subTest(style=text):
                self.assertIn(text, style)

        expected_script = [
            'const scmEvidencePanel = root.querySelector("#code-studio-scm-evidence-panel");',
            'const refreshScmEvidenceButton = root.querySelector("#code-studio-refresh-scm-evidence");',
            "function collectScmEvidenceSummary",
            "function renderScmEvidencePanel",
            "function evidenceEntryScope",
            "function evidenceFilterMatches",
            "function formatEvidenceDetail",
            "function replayScmEvidenceEntry",
            "normalizeEvidenceEntries",
            "recentComponentEvidence",
            "recentRouteEvidence",
            "evidenceSummary",
            "code-studio-scm-evidence-filter",
            "code-studio-replay-scm-evidence",
            "code-studio-export-scm-evidence-packet",
            "code-studio-download-scm-evidence-packet",
            "code-studio-scm-evidence-detail",
            "mcel-code-studio-scm-debug-packet",
            "SCM evidence replay",
            "SCM evidence debug packet exported",
            "SCM evidence refreshed",
        ]
        for text in expected_script:
            with self.subTest(script=text):
                self.assertIn(text, script)


    def test_code_studio_scm_evidence_export_packet_shape_is_stable(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        app = APP_PATH.read_text(encoding="utf-8")

        expected_markup = [
            'id="code-studio-export-scm-evidence-packet"',
            'id="code-studio-download-scm-evidence-packet"',
            "Export SCM Evidence Packet",
            "Download packet",
        ]
        for text in expected_markup:
            with self.subTest(markup=text):
                self.assertIn(text, app)

        expected_packet_shape = [
            'kind: "mcel-code-studio-scm-debug-packet"',
            "packetVersion: SCM_EVIDENCE_PACKET_VERSION",
            "MCEL_RUNTIME_PACKAGE_VERSION",
            "versions: {",
            "runtimePackage: MCEL_RUNTIME_PACKAGE_VERSION",
            "workspace: {",
            "route: {",
            "filters: {",
            "dirtyState: collectDirtyStateSummary(fields)",
            "gates: collectGateStatus(gates)",
            "evidence: {",
            "component: summary.componentPacket",
            "route: summary.routePacket",
            "selectedEvidence: formatEvidenceDetail(selectedEntry)",
            "lastReplayResult: studioState.lastScmReplayResult",
            "lastReport: report ? {",
            "copyScmEvidenceDebugPacket",
            "downloadScmEvidenceDebugPacket",
        ]
        for text in expected_packet_shape:
            with self.subTest(packet=text):
                self.assertIn(text, script)

        packet_start = script.index('kind: "mcel-code-studio-scm-debug-packet"')
        for later in [
            "versions: {",
            "workspace: {",
            "filters: {",
            "dirtyState:",
            "gates:",
            "evidence: {",
            "selectedEvidence:",
            "lastReplayResult:",
        ]:
            with self.subTest(order=later):
                self.assertGreater(script.index(later, packet_start), packet_start)


    def test_pretty_doc_explains_better_than_react_lane(self) -> None:
        doc = PRETTY_DOC.read_text(encoding="utf-8")
        expected = [
            "MCEL Code Studio",
            "React, Vue, Svelte, and Web Components are better choices",
            "source-safe-code-editor",
            "author-owned source is canonical",
            "generated editor chrome is runtime-only",
            "dirty runtime drafts do not serialize until committed",
            "Repair runtime chrome from the author-owned source",
            "Serialize clean source without generated runtime nodes",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, doc)
