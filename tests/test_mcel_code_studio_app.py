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
            'id="code-studio-save-live-workspace"',
            'id="code-studio-restore-live-workspace"',
            'id="code-studio-clear-live-workspace"',
            'id="code-studio-live-workspace-persistence"',
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


    def test_code_studio_flagship_workbench_regions_are_scroll_contained(self) -> None:
        style = STYLE_PATH.read_text(encoding="utf-8")
        expected = [
            "Patch 17B: flagship workbench containment",
            "#code-editor-app {",
            "height: min(900px, calc(100dvh - 32px));",
            "contain: layout paint;",
            "grid-template-columns: 48px clamp(220px, 17vw, 280px) minmax(0, 1fr) clamp(320px, 24vw, 400px);",
            ".code-editor-app .code-studio-editor-pane:not(.active)",
            "display: none !important;",
            '.code-editor-app .code-studio-editor-pane[data-code-studio-pane="contract"].active',
            "grid-template-rows: 42px minmax(0, 0.38fr) minmax(0, 0.62fr);",
            ".code-editor-app .code-studio-scm-evidence,",
            "grid-template-rows: auto auto minmax(0, 1fr);",
            ".code-editor-app .code-studio-inspector,",
            "grid-template-rows: minmax(0, 1fr) auto minmax(0, 0.42fr);",
            '.code-editor-app .code-studio-bottom-panel[data-expanded="true"]',
            "height: min(320px, 40%);",
            "@media (max-width: 1250px)",
            ".code-editor-app .code-studio-inspector",
            "display: grid;",
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
            "buildScmAiRepairPrompt",
            "exportScmAiRepairPrompt",
            "copyCurrentScmAiRepairPrompt",
            "buildScmReplaySnapshot",
            "compareScmReplaySnapshots",
            "formatScmReplayComparisonDetail",
            "persistLiveWorkspaceFromSource",
            "hydratePersistedLiveWorkspace",
            "clearPersistedLiveWorkspace",
            "collectLiveWorkspacePersistenceSummary",
            "LIVE_WORKSPACE_PERSISTENCE_KEY",
            "mcel-code-studio-live-workspace-persistence-record",
            "mcel-code-studio-live-workspace-persistence-summary",
            "Live workspace persisted through SCM saveFile effect and route loaders.",
            "replayScmEvidenceEntry",
            "code-studio-scm-evidence-entry",
            "code-studio-open-scm-evidence-detail",
            "renderSelectedEvidenceInProofDock",
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
            'id="code-studio-generate-scm-repair-prompt"',
            'id="code-studio-download-scm-evidence-packet"',
            'id="code-studio-proof-detail-panel"',
            "Replay selected gate",
            "Open proof dock",
            "Export SCM Evidence Packet",
            "Generate AI repair prompt",
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
            ".code-studio-scm-proof-summary-card",
            ".code-studio-scm-evidence-preview",
            ".code-studio-proof-detail-panel",
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
            "code-studio-generate-scm-repair-prompt",
            "code-studio-download-scm-evidence-packet",
            "code-studio-open-scm-evidence-detail",
            "code-studio-open-scm-replay-detail",
            "renderSelectedEvidenceInProofDock",
            "renderReplayComparisonInProofDock",
            "mcel-code-studio-scm-debug-packet",
            "mcel-code-studio-scm-replay-snapshot",
            "mcel-code-studio-scm-replay-comparison",
            "SCM evidence replay",
            "before/after snapshot comparison",
            "SCM evidence debug packet exported",
            "SCM AI repair prompt copied from evidence packet",
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
            'id="code-studio-generate-scm-repair-prompt"',
            'id="code-studio-download-scm-evidence-packet"',
            "Export SCM Evidence Packet",
            "Generate AI repair prompt",
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
            "lastReplaySnapshotComparison: studioState.lastScmReplaySnapshotComparison",
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
            "lastReplaySnapshotComparison:",
        ]:
            with self.subTest(order=later):
                self.assertGreater(script.index(later, packet_start), packet_start)


    def test_code_studio_scm_replay_snapshot_comparison_is_exported(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        app = APP_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")

        expected_markup = [
            'id="code-studio-scm-replay-comparison"',
            "Replay snapshot comparison",
            "Replay a selected gate to compare before/after SCM evidence",
        ]
        for text in expected_markup:
            with self.subTest(markup=text):
                self.assertIn(text, app)

        expected_script = [
            "SCM_REPLAY_SNAPSHOT_VERSION",
            "function buildScmReplaySnapshot",
            "function compareScmReplaySnapshots",
            "function formatScmReplayComparisonDetail",
            'kind: "mcel-code-studio-scm-replay-snapshot"',
            'kind: "mcel-code-studio-scm-replay-comparison"',
            "beforeSnapshot = buildScmReplaySnapshot",
            "afterSnapshot = buildScmReplaySnapshot",
            "lastReplaySnapshotComparison: studioState.lastScmReplaySnapshotComparison",
            "lastReplaySnapshotComparison: packet.lastReplaySnapshotComparison",
            "Replay selected gate to capture before/after SCM evidence snapshots.",
            "before/after snapshot comparison",
        ]
        for text in expected_script:
            with self.subTest(script=text):
                self.assertIn(text, script)

        expected_style = [
            ".code-studio-scm-replay-comparison",
            ".code-studio-scm-replay-comparison code",
        ]
        for text in expected_style:
            with self.subTest(style=text):
                self.assertIn(text, style)

        replay_start = script.index("function replayScmEvidenceEntry")
        before = script.index('buildScmReplaySnapshot("before"', replay_start)
        after = script.index('buildScmReplaySnapshot("after"', replay_start)
        compare = script.index("compareScmReplaySnapshots(beforeSnapshot, afterSnapshot", replay_start)
        self.assertLess(before, after)
        self.assertLess(after, compare)


    def test_code_studio_scm_ai_repair_prompt_is_contract_first(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        app = APP_PATH.read_text(encoding="utf-8")

        expected_markup = [
            'id="code-studio-generate-scm-repair-prompt"',
            "Generate AI repair prompt",
        ]
        for text in expected_markup:
            with self.subTest(markup=text):
                self.assertIn(text, app)

        expected_prompt_contract = [
            "SCM_AI_REPAIR_PROMPT_VERSION",
            "function buildScmAiRepairPrompt",
            "function exportScmAiRepairPrompt",
            "function copyCurrentScmAiRepairPrompt",
            "mcel-code-studio-scm-ai-repair-prompt-input",
            "MCEL STRICT COMPOSITION MODEL AI REPAIR PROMPT",
            "contract-first UI system for AI-written software",
            "Do not treat MCEL as a React, Vue, Angular, or Svelte clone.",
            "Allowed repair surface:",
            "Forbidden changes:",
            "Do not introduce undeclared DOM, source, route, state, or runtime reads/writes.",
            "Do not serialize runtime-only editor chrome or assistant/debug UI into author-owned source.",
            "Do not auto-run mutating transitions or repair strategies without explicit user action.",
            "SCM evidence packet JSON:",
            "copyScmText(output.prompt)",
            "SCM AI repair prompt copied from evidence packet",
        ]
        for text in expected_prompt_contract:
            with self.subTest(prompt=text):
                self.assertIn(text, script)

        prompt_start = script.index("function buildScmAiRepairPrompt")
        for later in [
            "Allowed repair surface:",
            "Forbidden changes:",
            "SCM evidence packet JSON:",
            "function exportScmAiRepairPrompt",
            "function copyCurrentScmAiRepairPrompt",
        ]:
            with self.subTest(order=later):
                self.assertGreater(script.index(later, prompt_start), prompt_start)


    def test_code_studio_live_workspace_persistence_uses_route_and_effect_evidence(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        app = APP_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")

        expected_markup = [
            'id="code-studio-save-live-workspace"',
            'id="code-studio-restore-live-workspace"',
            'id="code-studio-clear-live-workspace"',
            'id="code-studio-live-workspace-persistence"',
            "Save live workspace",
            "Restore saved workspace",
            "Clear saved workspace",
            "live workspace persistence: not saved",
        ]
        for text in expected_markup:
            with self.subTest(markup=text):
                self.assertIn(text, app)

        expected_style = [
            ".code-studio-live-workspace-persistence",
            "data-status",
            "text-overflow: ellipsis;",
        ]
        for text in expected_style:
            with self.subTest(style=text):
                self.assertIn(text, style)

        expected_script = [
            "LIVE_WORKSPACE_PERSISTENCE_VERSION",
            "LIVE_WORKSPACE_PERSISTENCE_KEY",
            "main-computer-code-studio-live-workspace-v1",
            "function liveWorkspaceStorage",
            "function persistLiveWorkspaceFromSource",
            "function hydratePersistedLiveWorkspace",
            "function clearPersistedLiveWorkspace",
            "function collectLiveWorkspacePersistenceSummary",
            "function renderLiveWorkspacePersistenceStatus",
            'runScmGate("effect:saveFile"',
            "enterScmRouteAndRunLoaders({forceEnter: true})",
            "mcel-code-studio-live-workspace-persistence-record",
            "mcel-code-studio-live-workspace-persistence-summary",
            "persistence: collectLiveWorkspacePersistenceSummary()",
            "persistence: packet.persistence",
            "persistenceStatus=",
            "live workspace persistence boundaries",
            'persistLiveWorkspaceFromSource("commitDraft"',
            "hydratePersistedLiveWorkspace();",
            "code-studio-save-live-workspace",
            "code-studio-restore-live-workspace",
            "code-studio-clear-live-workspace",
        ]
        for text in expected_script:
            with self.subTest(script=text):
                self.assertIn(text, script)

        commit_start = script.index("function commitRuntimeDraft")
        save_effect = script.index('runScmGate("effect:saveFile"', commit_start)
        route_loader = script.index("enterScmRouteAndRunLoaders({forceEnter: true})", save_effect)
        persist = script.index('persistLiveWorkspaceFromSource("commitDraft"', route_loader)
        self.assertLess(save_effect, route_loader)
        self.assertLess(route_loader, persist)

        packet_start = script.index('kind: "mcel-code-studio-scm-debug-packet"')
        persistence = script.index("persistence: collectLiveWorkspacePersistenceSummary()", packet_start)
        gates = script.index("gates: collectGateStatus(gates)", packet_start)
        self.assertLess(persistence, gates)


    def test_code_studio_scm_contract_authoring_helper_guides_generated_apps(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        app = APP_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")

        expected_markup = [
            'id="code-studio-generate-scm-contract-helper"',
            'id="code-studio-scm-contract-authoring-helper"',
            "Generate contract helper",
            "SCM contract authoring helper",
            "contract-first starter for AI-created MCEL apps",
        ]
        for text in expected_markup:
            with self.subTest(markup=text):
                self.assertIn(text, app)

        expected_script = [
            "SCM_CONTRACT_AUTHORING_HELPER_VERSION",
            "function buildScmContractAuthoringHelper",
            "function formatScmContractAuthoringHelper",
            "function exportScmContractAuthoringHelper",
            "function copyCurrentScmContractAuthoringHelper",
            "mcel-code-studio-scm-contract-authoring-helper",
            "mcel-code-studio-scm-contract-authoring-helper-export",
            "MCEL SCM CONTRACT AUTHORING HELPER",
            "strict SCM contract starter for generated MCEL apps",
            "Declare ownership before rendering UI.",
            "Declare reads and writes before loading data or mutating state.",
            "component ownership",
            "child composition",
            "route params/query/loaders",
            "effect triggers/reads/writes/cancellation/race policy",
            "layout/style computed gates",
            "serialization boundaries",
            "repair guards and replay safety",
            "contractAuthoring: studioState.lastScmContractAuthoringExport",
            "contractAuthoring: packet.contractAuthoring",
            "SCM contract authoring helper copied for generated apps",
        ]
        for text in expected_script:
            with self.subTest(script=text):
                self.assertIn(text, script)

        expected_style = [
            ".code-studio-scm-contract-authoring-helper",
            ".code-studio-scm-contract-authoring-helper code",
        ]
        for text in expected_style:
            with self.subTest(style=text):
                self.assertIn(text, style)

        helper_start = script.index("function buildScmContractAuthoringHelper")
        for later in [
            "authoringPrinciples:",
            "contractSkeleton:",
            "authoringChecklist:",
            "function formatScmContractAuthoringHelper",
            "function exportScmContractAuthoringHelper",
            "function copyCurrentScmContractAuthoringHelper",
        ]:
            with self.subTest(order=later):
                self.assertGreater(script.index(later, helper_start), helper_start)


    def test_code_studio_flagship_skeleton_has_tabbed_scm_ai_inspector(self) -> None:
        app = APP_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        expected_markup = [
            'id="code-studio-flagship-inspector"',
            "Tabbed SCM / AI Inspector",
            "Contract · Evidence · Runtime · AI",
            'id="code-studio-scm-ai-tab-contract"',
            'id="code-studio-scm-ai-tab-evidence"',
            'id="code-studio-scm-ai-tab-runtime"',
            'id="code-studio-scm-ai-tab-ai"',
            'id="code-studio-scm-ai-panel-contract"',
            'id="code-studio-scm-ai-panel-evidence"',
            'id="code-studio-scm-ai-panel-runtime"',
            'id="code-studio-scm-ai-panel-ai"',
            'id="code-studio-flagship-contract-summary"',
            'id="code-studio-flagship-evidence-summary"',
            'id="code-studio-flagship-runtime-summary"',
            'id="code-studio-flagship-ai-summary"',
            'data-code-studio-scm-ai-action="copy-prompt"',
            'data-code-studio-scm-ai-action="copy-helper"',
            'data-code-studio-scm-ai-action="copy-packet"',
            "Bottom Proof Dock",
            "Diagnostics · Evidence Detail · Replay · Serialization · Persistence · Logs",
        ]
        for text in expected_markup:
            with self.subTest(markup=text):
                self.assertIn(text, app)

        expected_style = [
            ".code-studio-scm-ai-inspector",
            ".code-studio-scm-ai-tabs",
            ".code-studio-scm-ai-tabs button.active",
            ".code-studio-scm-ai-panel",
            ".code-studio-scm-ai-card",
            ".code-studio-scm-ai-actions",
            ".code-studio-command-pill[data-ok=\"true\"]",
            ".code-studio-command-pill[data-ok=\"false\"]",
            ".code-studio-proof-dock-tabs",
        ]
        for text in expected_style:
            with self.subTest(style=text):
                self.assertIn(text, style)

        expected_script = [
            'const flagshipInspector = root.querySelector("#code-studio-flagship-inspector");',
            'const topRouteStatus = root.querySelector("#code-studio-top-route-status");',
            'activeScmInspectorTab: "contract"',
            "function setInspectorPanel",
            "function buildFlagshipInspectorModel",
            "function renderFlagshipInspector",
            "updateTopCommandStatus",
            "currentScmRouteKey(routeParamsForScm(fields), routeQueryForScm())",
            "codeStudioScmAiTab",
            "codeStudioScmAiAction",
            "copyCurrentScmAiRepairPrompt",
            "copyCurrentScmContractAuthoringHelper",
            "copyCurrentScmEvidenceDebugPacket",
            "Contract = what should be true, Evidence = what is proven, Runtime = what happened, AI = what may change next.",
        ]
        for text in expected_script:
            with self.subTest(script=text):
                self.assertIn(text, script)

        self.assertNotIn("routeKeyForScm", script)

        inspector = app.index('id="code-studio-flagship-inspector"')
        contract = app.index('id="code-studio-scm-ai-tab-contract"', inspector)
        evidence = app.index('id="code-studio-scm-ai-tab-evidence"', inspector)
        runtime = app.index('id="code-studio-scm-ai-tab-runtime"', inspector)
        ai = app.index('id="code-studio-scm-ai-tab-ai"', inspector)
        self.assertLess(contract, evidence)
        self.assertLess(evidence, runtime)
        self.assertLess(runtime, ai)



    def test_code_studio_flagship_workbench_regions_are_hard_split(self) -> None:
        app = APP_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        expected_markup = [
            'data-code-studio-workbench-region="main-grid"',
            'data-code-studio-workbench-region="mode-rail"',
            'data-code-studio-workbench-region="workspace-sidebar"',
            'data-code-studio-workbench-region="editor-workbench"',
            'data-code-studio-workbench-region="scm-ai-inspector"',
            'data-code-studio-workbench-region="proof-dock"',
            'class="code-studio-bottom-panel code-studio-proof-dock"',
        ]
        for text in expected_markup:
            with self.subTest(markup=text):
                self.assertIn(text, app)

        expected_style = [
            "Patch 17C: hard workbench region split",
            'body:has(#code-editor-app)',
            'grid-template-areas:',
            'data-code-studio-workbench-region="main-grid"',
            'data-code-studio-workbench-region="scm-ai-inspector"',
            'data-code-studio-workbench-region="proof-dock"',
            '.code-studio-proof-dock:not([data-expanded="true"]) .code-studio-aider-shell',
            'overflow: hidden !important',
        ]
        for text in expected_style:
            with self.subTest(style=text):
                self.assertIn(text, style)

        expected_script = [
            'function prepareFlagshipWorkbenchRegions',
            'root.dataset.workbenchSplit = "flagship-region-split"',
            'body.dataset.codeStudioWorkbenchRegion = "main-grid"',
            'inspector.dataset.codeStudioWorkbenchRegion = "scm-ai-inspector"',
            'dock.dataset.codeStudioWorkbenchRegion = "proof-dock"',
            'if (dock.dataset.expanded !== "true") dock.dataset.expanded = "false"',
            'prepareFlagshipWorkbenchRegions();',
        ]
        for text in expected_script:
            with self.subTest(script=text):
                self.assertIn(text, script)


    def test_code_studio_compact_viewport_keeps_inspector_visible(self) -> None:
        style = STYLE_PATH.read_text(encoding="utf-8")
        expected = [
            "Patch 17E: inspector visibility and bounded-workbench containment",
            "@media (max-width: 720px)",
            'grid-template-rows: minmax(0, 1fr) minmax(210px, 34dvh) !important;',
            'data-code-studio-workbench-region="scm-ai-inspector"]',
            "display: grid !important;",
            "grid-row: 2 !important;",
            "border-top: 1px solid #2d2d30 !important;",
            "collapsed proof dock cannot leak long proof/detail payloads",
            ".code-studio-proof-detail-panel",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, style)

        self.assertNotIn(
            '#code-editor-app [data-code-studio-workbench-region="scm-ai-inspector"] {\n'
            '    display: none !important;',
            style,
        )




    def test_code_studio_inactive_app_guard_prevents_global_overlay(self) -> None:
        app = APP_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")

        self.assertIn('id="code-editor-app" style="display: none;"', app)

        expected = [
            "Patch 17E4: inactive app guard",
            'html:has(body:not([data-active-app="code-editor"]) #code-editor-app)',
            'body:not([data-active-app="code-editor"]):has(#code-editor-app)',
            'body:not([data-active-app="code-editor"]) #code-editor-app',
            "display: none !important;",
            "position: static !important;",
            "height: auto !important;",
            "overflow: auto !important;",
            "The applications page keeps every app root in the DOM",
            "fixed workbench active-app-only",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, style)

        marker = "Patch 17E4: inactive app guard"
        self.assertGreater(style.index(marker), style.index("Patch 17E2: force bounded shell"))


    def test_code_studio_force_bounded_shell_matches_browser_twiddle(self) -> None:
        style = STYLE_PATH.read_text(encoding="utf-8")
        expected = [
            "Patch 17E2: force bounded shell / inspector containment",
            "position: fixed !important;",
            "inset: 0 !important;",
            "height: 100dvh !important;",
            "grid-template-columns: 44px 260px minmax(0, 1fr) 360px !important;",
            "grid-template-rows: minmax(0, 1fr) minmax(220px, 34dvh) !important;",
            'data-code-studio-workbench-region="scm-ai-inspector"]',
            "visibility: visible !important;",
            "height: 34px !important;",
            "max-height: 34px !important;",
            "z-index: 9999 !important;",
            "border-top: 1px solid rgba(255, 213, 90, 0.35) !important;",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, style)

    def test_code_studio_css_rules_are_not_trapped_in_unclosed_repeater_rule(self) -> None:
        style = STYLE_PATH.read_text(encoding="utf-8")
        compact_option_rule = (
            '[data-mc-widget-kind="repeater"][data-mc-item-display-preset="compact"] option {\n'
            "      min-height: 18px;\n"
            "    }"
        )
        self.assertIn(compact_option_rule, style)
        self.assertEqual(style.count("{"), style.count("}"))

        source_safe_marker = "/* MCEL Code Studio flagship example"
        force_bounded_marker = "Patch 17E2: force bounded shell / inspector containment"
        before_source_safe = style[: style.index(source_safe_marker)]
        before_force_bounded = style[: style.index(force_bounded_marker)]
        self.assertEqual(before_source_safe.count("{"), before_source_safe.count("}"))
        self.assertEqual(before_force_bounded.count("{"), before_force_bounded.count("}"))


    def test_code_studio_routes_long_proof_payloads_to_bottom_dock(self) -> None:
        app = APP_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        expected_markup = [
            'id="code-studio-proof-detail-panel"',
            'Open proof dock',
            'data-mc-component-id="code-editor.studio.proof-detail"',
        ]
        for text in expected_markup:
            with self.subTest(markup=text):
                self.assertIn(text, app)

        expected_style = [
            "Patch 17D: workbench content routing twiddle",
            ".code-studio-scm-proof-summary-card",
            ".code-studio-scm-evidence-preview",
            ".code-studio-proof-detail-panel",
            ".code-studio-proof-detail-output",
            ".code-studio-scm-evidence-drilldown",
            "Long proof/debug payloads are only allowed in the Bottom Proof Dock.",
        ]
        for text in expected_style:
            with self.subTest(style=text):
                self.assertIn(text, style)

        expected_script = [
            "function setProofDockExpanded",
            "function renderProofDockPayload",
            "function renderSelectedEvidenceInProofDock",
            "function renderReplayComparisonInProofDock",
            "function renderContractHelperInProofDock",
            "Open evidence detail in proof dock",
            "code-studio-scm-proof-summary-card",
            "code-studio-scm-evidence-preview",
            "Center workbench is authoring-first.",
            "SCM evidence summary refreshed without leaving the active editor pane.",
            "return scmEvidenceSummary;",
        ]
        for text in expected_script:
            with self.subTest(script=text):
                self.assertIn(text, script)

        self.assertNotIn('              <code>${escapeHtml(JSON.stringify(selectedDetail, null, 2))}</code>', script)
        self.assertNotIn('        showPane("contract");\n      }\n\n      function serializeCleanSource', script)
        self.assertNotIn('renderScmEvidencePanel(studioState.lastReport);\n        showPane("contract");', script)


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
