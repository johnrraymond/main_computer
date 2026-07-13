from __future__ import annotations

from pathlib import Path
import json
import shutil
import subprocess
import unittest

from main_computer.viewport import APPLICATIONS_INDEX_HTML


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "main_computer" / "web" / "applications" / "apps" / "code-editor.html"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
STYLE_PATH = ROOT / "main_computer" / "web" / "applications" / "styles" / "code-editor.css"
SCRIPT_PATH = ROOT / "main_computer" / "web" / "applications" / "scripts" / "code-editor-mcel-studio.js"
LAYOUT_CONTRACT_PATH = ROOT / "main_computer" / "web" / "applications" / "scripts" / "code-editor-layout-contract.js"
MONACO_ADAPTER_PATH = ROOT / "main_computer" / "web" / "applications" / "scripts" / "code-editor-monaco-adapter.js"
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
            "applications/scripts/code-editor-monaco-adapter.js",
            "applications/scripts/code-editor-mcel-studio.js",
        ]
        applications_html = APPLICATIONS_HTML.read_text(encoding="utf-8")
        positions = [applications_html.index(include) for include in include_order]

        self.assertEqual(positions, sorted(positions))

    def test_code_studio_mounts_monaco_as_runtime_only_editor_adapter(self) -> None:
        app = APP_PATH.read_text(encoding="utf-8")
        applications_html = APPLICATIONS_HTML.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        adapter = MONACO_ADAPTER_PATH.read_text(encoding="utf-8")

        expected_markup = [
            "Monaco mounts as runtime-only editor chrome",
            'id="code-studio-runtime-monaco"',
            'data-code-studio-monaco-runtime="host"',
            'data-code-studio-monaco-fallback="textarea"',
            "fallback runtime draft",
            "commitDraft is source gate",
        ]
        for text in expected_markup:
            with self.subTest(markup=text):
                self.assertIn(text, app + script)

        expected_application_order = [
            "applications/scripts/code-editor-scm-manifest.js",
            "applications/scripts/code-editor-monaco-adapter.js",
            "applications/scripts/code-editor-mcel-studio.js",
        ]
        positions = [applications_html.index(include) for include in expected_application_order]
        self.assertEqual(positions, sorted(positions))

        expected_style = [
            ".code-studio-monaco-host",
            '.code-studio-monaco-host[data-monaco-outcome="pass"]',
            '.code-studio-runtime-editor[data-monaco-mounted="true"] .code-studio-runtime-fallback',
            ".code-studio-runtime-fallback",
        ]
        for text in expected_style:
            with self.subTest(style=text):
                self.assertIn(text, style)

        expected_script = [
            "function resolveMonacoAdapter",
            "function mountRuntimeMonaco",
            "function recordMonacoRuntimeReceipt",
            "function updateRuntimeDraftFromEditor",
            "editor.monaco.load",
            "editor.monaco.mount",
            "editor.monaco.change",
            "editor.monaco.layoutObserved",
            "editor.monaco.dispose",
            "adapter.getValue",
            "Monaco mounted as a runtime-only draft editor. Commit editor draft remains the source mutation gate.",
        ]
        for text in expected_script:
            with self.subTest(script=text):
                self.assertIn(text, script)

        expected_adapter = [
            "MainComputerMonacoAdapter",
            "LOCAL_VS_BASE",
            "CDN_VS_BASE",
            "loadViaAmd",
            "createModel",
            "onDidChangeContent",
            "disposeActive",
            "mcel-code-studio-monaco-runtime-receipt",
            "file-protocol-workers-unsupported",
        ]
        for text in expected_adapter:
            with self.subTest(adapter=text):
                self.assertIn(text, adapter)

        commit_start = script.index("function commitRuntimeDraft")
        monaco_value = script.index("adapter.getValue", commit_start)
        edit_gate = script.index('runScmTransition("editDraft"', monaco_value)
        commit_gate = script.index('runScmTransition("commitDraft"', edit_gate)
        self.assertLess(monaco_value, edit_gate)
        self.assertLess(edit_gate, commit_gate)


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


    def test_code_studio_scm_regression_harness_covers_monaco_and_replay(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        app = APP_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")

        expected_markup = [
            'id="code-studio-run-scm-regression-harness"',
            'id="code-studio-scm-regression-harness"',
            "SCM regression harness",
            "validation, runtime mount, Monaco boundary, generic receipt fixtures, replay snapshots, and clean serialization",
        ]
        for text in expected_markup:
            with self.subTest(markup=text):
                self.assertIn(text, app)

        expected_script = [
            "SCM_REGRESSION_HARNESS_VERSION",
            "function buildScmRegressionSourceSnapshot",
            "function compareScmRegressionSourceSnapshots",
            "function runScmRegressionScenario",
            "function runScmRegressionHarness",
            "function formatScmRegressionHarnessDetail",
            "function formatScmReplayExpectationFailuresDetail",
            "function renderScmRegressionHarnessInProofDock",
            "function renderScmReplayExpectationFailuresInProofDock",
            "function summarizeScmReplayComparisonForWorkbench",
            "function summarizeScmReplayExpectationsForWorkbench",
            "function summarizeScmReplayFixturePackForWorkbench",
            "disposition",
            "const safetyOk = sourceSafety.sourceUnchanged && sourceSafety.runtimeChromeStayedOutOfSource;",
            'scenario.disposition === "BLOCKED"',
            'scenario.disposition === "EXCEPTION"',
            'detail?.disposition === "MISMATCH"',
            "PASS",
            "BLOCKED",
            "EXCEPTION",
            "MISMATCH",
            "function buildGenericScmReplayFixtures",
            "function runGenericScmReplayFixturePack",
            "function assertGenericScmReplayFixtureVector",
            'kind: "mcel-code-studio-scm-regression-harness"',
            'kind: "mcel-code-studio-scm-regression-source-snapshot"',
            'id="code-studio-run-scm-regression-harness"',
            'id="code-studio-open-scm-regression-detail"',
            'id="code-studio-open-scm-replay-expectation-failures"',
            "Open replay expectation failures in proof dock",
            "Replay expectation failures",
            "Fixture pack status",
            "Mismatch-first findings",
            "replay-expectation-failures",
            "copy-replay-expectation-failures",
            "mcel-code-studio-replay-expectation-workbench-summary",
            "mcel-code-studio-replay-workbench-summary",
            "source.validation",
            "runtime.mount-source-safe",
            "monaco.runtime-boundary",
            "generic.wallet-fixtures",
            "generic.code-editor-fixtures",
            "replay.snapshot-comparison",
            "serialization.clean-source",
            "lastRegressionHarness: studioState.lastScmRegressionHarness",
            "runScmRegressionHarness,",
            "renderScmRegressionHarnessInProofDock,",
            "renderScmReplayExpectationFailuresInProofDock,",
            "summarizeScmReplayExpectationsForWorkbench,",
            "buildGenericScmReplayFixtures,",
            "runGenericScmReplayFixturePack,",
        ]
        for text in expected_script:
            with self.subTest(script=text):
                self.assertIn(text, script)

        self.assertNotIn('scenario.actionOk === false && !scenario.exception', script)
        self.assertNotIn('const disposition = exception ? "EXCEPTION"', script)

        expected_style = [
            ".code-studio-scm-regression-harness",
            ".code-studio-scm-regression-harness code",
            ".code-studio-proof-detail-summary",
            ".code-studio-proof-detail-issues",
        ]
        for text in expected_style:
            with self.subTest(style=text):
                self.assertIn(text, style)

        harness_start = script.index("function runScmRegressionHarness")
        scenario_order = [
            "source.validation",
            "runtime.mount-source-safe",
            "monaco.runtime-boundary",
            "generic.wallet-fixtures",
            "generic.code-editor-fixtures",
            "replay.snapshot-comparison",
            "serialization.clean-source",
        ]
        positions = [script.index(name, harness_start) for name in scenario_order]
        self.assertEqual(positions, sorted(positions))


    def test_code_studio_generic_replay_fixture_harness_covers_wallet_and_editor_contracts(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        expected_script = [
            'const SCM_REPLAY_FIXTURE_HARNESS_VERSION = "1.2.0";',
            "function buildGenericScmReplayFixtureVector",
            "function buildWalletScmReplayFixtureReceipt",
            "function buildWalletScmReplayFixtures",
            "function buildCodeEditorScmReplayFixtures",
            "function assertGenericScmReplayFixtureVector",
            "function classifyScmReplayFixtureDisposition",
            "function buildScmReplayFixtureExpectationComparison",
            "function runGenericScmReplayFixturePack",
            "formatScmDispositionCounts",
            "fixturePackStatus",
            "replayExpectationFailures",
            'kind: "mcel-code-studio-generic-scm-replay-fixture-pack"',
            "wallet.connect.pass",
            "wallet.connect.blocked",
            "wallet.connect.exception",
            "wallet.accountsChanged.switch",
            "wallet.accountsChanged.disconnect",
            "wallet.chainChanged.wrong-chain",
            "wallet.draftTx.pass",
            "wallet.draftTx.blocked-wallet",
            "wallet.draftTx.blocked-chain",
            "wallet.repairPacket.generated",
            "wallet.repairPacket.forbidden-write-blocked",
            "code-editor.monaco.mount.pass",
            "code-editor.monaco.mount.blocked",
            "code-editor.monaco.mount.exception",
            "code-editor.monaco.change.draft-only",
            "code-editor.editorDraft.created.provenance",
            "code-editor.editorDraft.changed.provenance",
            "code-editor.editorDraft.committed.provenance",
            "code-editor.editorDraft.discarded.provenance",
            "code-editor.editorDraft.restored.provenance",
            "code-editor.commitDraft.source-gate",
            "code-editor.serialization.clean-source",
            "code-editor.layout.observe.pass",
            "code-editor.layout.observe.fail",
            "runtimeOnlyWrites",
            "expectedDisposition",
            "expectedDisposition expected",
            "observedDisposition",
            "dispositionCounts",
            "mismatchCount",
            "fixtureMismatches",
            "MISMATCH",
            "sourceUnchanged",
            "sourceWriteEffect",
            "sourceMutationGate",
            "draftProvenanceEventType",
            "draftRuntimeOnlyUntilCommit",
            "sourceMutationsOnlyByCommitDraft",
            "txDraftNoSend",
            "txDraftProvenanceRecorded",
            "txDraftInvalidatedByContain",
            "txDraftBoundary provenance was not recorded",
            "txDraft invalidation missing",
            "txDraft.provenance.v1",
            "sourceRequestHash",
            "walletAccountHash",
            "chainProof",
            "probeEnvelopeIds",
            "accountInvalidatedDraftBoundary",
            "chainInvalidatedDraftBoundary",
            "repairBoundaryBlocked",
            "layoutViolationCount",
            "fixturePacks: harness.fixturePacks || []",
        ]
        for text in expected_script:
            with self.subTest(script=text):
                self.assertIn(text, script)

        wallet = script.index("function buildWalletScmReplayFixtures")
        editor = script.index("function buildCodeEditorScmReplayFixtures")
        pack = script.index("function buildGenericScmReplayFixtures")
        assert_fn = script.index("function assertGenericScmReplayFixtureVector")
        runner = script.index("function runGenericScmReplayFixturePack")
        self.assertLess(wallet, pack)
        self.assertLess(editor, pack)
        self.assertLess(pack, assert_fn)
        self.assertLess(assert_fn, runner)

        harness_start = script.index("function runScmRegressionHarness")
        wallet_scenario = script.index("generic.wallet-fixtures", harness_start)
        editor_scenario = script.index("generic.code-editor-fixtures", harness_start)
        provenance_scenario = script.index("editorDraft.provenance-boundary", harness_start)
        replay_scenario = script.index("replay.snapshot-comparison", harness_start)
        self.assertLess(wallet_scenario, editor_scenario)
        self.assertLess(editor_scenario, provenance_scenario)
        self.assertLess(provenance_scenario, replay_scenario)


    def test_code_studio_editor_draft_provenance_is_visible_and_commit_gated(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        app = APP_PATH.read_text(encoding="utf-8")

        expected_markup = [
            "Draft Provenance",
        ]
        for text in expected_markup:
            with self.subTest(markup=text):
                self.assertIn(text, app)

        expected_script = [
            "SCM_DRAFT_PROVENANCE_VERSION",
            "EDITOR_DRAFT_PROVENANCE_EFFECTS",
            "function recordEditorDraftProvenance",
            "function collectEditorDraftProvenanceSummary",
            "function formatEditorDraftProvenanceDetail",
            "function renderEditorDraftProvenanceInProofDock",
            "code-studio-open-draft-provenance-detail",
            "editorDraft.created",
            "editorDraft.changed",
            "editorDraft.restored",
            "editorDraft.committed",
            "editorDraft.discarded",
            "sourceMutationGate: \"commitDraft\"",
            "runtimeOnlyUntilCommit",
            "serializationExcludedUntilCommit",
            "sourceMutationsOnlyByCommitDraft",
            "Draft provenance",
        ]
        for text in expected_script:
            with self.subTest(script=text):
                self.assertIn(text, script)

        record_fn = script.index("function recordEditorDraftProvenance")
        update_fn = script.index("function updateRuntimeDraftFromEditor")
        commit_fn = script.index("function commitRuntimeDraft")
        self.assertLess(record_fn, update_fn)
        self.assertLess(update_fn, commit_fn)
        self.assertIn('recordEditorDraftProvenance("changed"', script[update_fn:commit_fn])
        self.assertIn('recordEditorDraftProvenance("committed"', script[commit_fn:])

    def test_code_studio_layout_gate_uses_component_owned_viewport_metrics(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        expected_script = [
            "const rootComputed = typeof window.getComputedStyle === \"function\" ? window.getComputedStyle(root) : null;",
            "rootIsBoundedViewport",
            "ownedDocumentHeight",
            "pageHeight",
            "rootPosition",
            "rootOverflow",
            "documentHeightRatio: rootHeight ? documentHeight / rootHeight : 1",
            "function summarizeLayoutGateViolations",
            "layoutViolations: summarizeLayoutGateViolations(report.scm)",
        ]
        for text in expected_script:
            with self.subTest(script=text):
                self.assertIn(text, script)

        observation_start = script.index("function collectLayoutObservation")
        bounded = script.index("rootIsBoundedViewport", observation_start)
        owned_height = script.index("ownedDocumentHeight", bounded)
        ratio = script.index("documentHeightRatio", owned_height)
        self.assertLess(bounded, owned_height)
        self.assertLess(owned_height, ratio)


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
            "SCM Receipt Inspector",
            "Contract · Effects · Runtime · Repair",
            'id="code-studio-scm-ai-tab-contract"',
            'id="code-studio-scm-ai-tab-evidence"',
            'id="code-studio-scm-ai-tab-runtime"',
            'id="code-studio-scm-ai-tab-ai"',
            'id="code-studio-scm-ai-panel-contract"',
            'id="code-studio-scm-ai-panel-evidence"',
            'id="code-studio-scm-ai-panel-runtime"',
            'id="code-studio-scm-ai-panel-ai"',
            'id="code-studio-flagship-receipt-summary"',
            'id="code-studio-flagship-contract-summary"',
            'id="code-studio-flagship-selected-effect-summary"',
            'id="code-studio-flagship-effect-graph"',
            'id="code-studio-flagship-actionable-gaps"',
            'id="code-studio-flagship-evidence-summary"',
            'id="code-studio-flagship-current-runtime-summary"',
            'id="code-studio-flagship-proof-history-summary"',
            'id="code-studio-flagship-runtime-summary"',
            'id="code-studio-flagship-ai-summary"',
            'data-code-studio-scm-ai-action="copy-prompt"',
            'data-code-studio-scm-ai-action="copy-helper"',
            'data-code-studio-scm-ai-action="copy-packet"',
            "Evidence and History",
            "Receipts · Effects · Runtime · Repair · Draft Provenance · Aider history · Documentation · Logs",
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
            "Patch 18E: Code Studio SCM receipt surface",
            ".code-studio-scm-receipt-card",
            ".code-studio-scm-effect-graph",
            ".code-studio-scm-effect-node",
            ".code-studio-scm-gap-list",
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
            "function collectContractEffectSurface",
            "function buildScmReceiptSurfaceModel",
            "function summarizeTxDraftProvenanceForWorkbench",
            "function formatNormalizedScmReceiptVectorDetail",
            "function renderScmReceiptVectorInProofDock",
            'id="code-studio-open-scm-receipt-vector-detail"',
            "function renderScmEffectGraph",
            "function renderActionableScmGaps",
            "function buildFlagshipInspectorModel",
            "function renderFlagshipInspector",
            "updateTopCommandStatus",
            "currentScmRouteKey(routeParamsForScm(fields), routeQueryForScm())",
            "codeStudioScmAiTab",
            "codeStudioScmAiAction",
            "copyCurrentScmAiRepairPrompt",
            "copyCurrentScmContractAuthoringHelper",
            "copyCurrentScmEvidenceDebugPacket",
            "Contract = what must hold, Effects = what may mutate, Runtime = what is current, Repair = what may change next.",
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


    def test_code_studio_normalizes_lab_receipt_vector_before_rendering(self) -> None:
        script = SCRIPT_PATH.read_text(encoding="utf-8")

        expected_script = [
            'const SCM_RECEIPT_VECTOR_VERSION = "1.0.0";',
            'const SCM_LAB_RECEIPT_KIND = "mcel-lab-medium-scm-proven-dev-network-app-receipt";',
            'const SCM_LAB_RECEIPT_PROOF_KIND = "mcel-code-studio-normalized-lab-receipt-vector";',
            "SCM_LAB_RECEIPT_EFFECT_SURFACE",
            '"wallet.connect": {',
            '"wallet.provider.accountsChanged": {',
            '"release.draftTx": {',
            '"ai.repairWalletHint": {',
            '"runtime-only-no-send"',
            '"runtime.proofChip", "runtime.repairPacket", "runtime.assistantRepairPrompt", "runtime.evidenceStrip"',
            '"source.devRelease", "runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.externalOutcome"',
            "function normalizeMcelLabReceiptVector",
            "function normalizeScmReceiptVector",
            "function ingestScmReceiptVector",
            "function collectScmReceiptVector",
            "function findMcelLabReceiptPayload",
            "function scmReceiptSelectedEvidenceKey",
            "function buildScmReceiptSourceAuthority",
            "function attachScmReceiptSourceAuthority",
            "function summarizeScmReceiptSourceForWorkbench",
            "function normalizeTxDraftInvalidationReasons",
            "function summarizeTxDraftProvenanceForWorkbench",
            "function formatNormalizedScmReceiptVectorDetail",
            "function renderScmReceiptVectorInProofDock",
            'document.querySelector("#mcel-tiny-contract-evidence")',
            "node?.__mcelReceiptPayload",
            "window.__mcelLabReceiptPayload",
            "actionOutcome",
            "externalOutcome",
            "governanceOutcome",
            "safetyOutcome",
            "proofCompleteness",
            "selectedEffect",
            "declaredReads",
            "declaredWrites",
            "nextAction",
            "repairPacket",
            "txDraftBoundary",
            "layoutObservation",
            "mcel-code-studio-tx-draft-provenance-workbench-summary",
            "txDraftConsumerGate",
            "consumerGate",
            "consumer blocked",
            "Tx draft consumer gate",
            "txDraft consumer gate blocked",
            "endgamePreflight",
            "sendSignPreflightStatus",
            "sendSignPreflightLabel",
            "Send/sign preflight",
            "locked-no-draft · canSend=false canSign=false canBroadcast=false",
            "mcel-code-studio-normalized-scm-receipt-workbench-detail",
            "replayWorkbench",
            "replayExpectations",
            "mcel-code-studio-receipt-source-authority",
            "mcel-code-studio-receipt-source-workbench-summary",
            "Receipt source",
            "Receipt freshness",
            "Receipt authority",
            "selected SCM evidence",
            "live validation report",
            "cached previous vector",
            "not ingested",
            "staleReason",
            'provenance.valid === true',
            '"provenance present"',
            'freshnessStatus === "stale"',
            "provenancePresent: hasProvenance",
            "provenanceEnforced",
            "noSendBoundaryPreserved",
            "Tx draft action",
            "rebuild draft from current receipt",
            "consumerGateStatus",
            "consumerGateReasons",
            "consumerGateAction",
            "rebuild draft to prove freshness",
            "Tx draft provenance",
            "Receipt Vector in Bottom Proof Dock",
            "Open receipt vector in proof dock",
            "normalized-receipt-vector",
            "copy-normalized-receipt-vector",
            "receiptVector: collectScmReceiptVector(report, summary, selectedEntry)",
            "const receiptVector = collectScmReceiptVector(studioState.lastReport, summary, selectedEvidence);",
            "normalizeScmReceiptVector,",
            "ingestScmReceiptVector,",
            "collectScmReceiptVector,",
            "summarizeScmReceiptSourceForWorkbench,",
            "summarizeTxDraftProvenanceForWorkbench,",
            "formatNormalizedScmReceiptVectorDetail,",
            "renderScmReceiptVectorInProofDock,",
        ]
        for text in expected_script:
            with self.subTest(script=text):
                self.assertIn(text, script)

        normalizer = script.index("function normalizeMcelLabReceiptVector")
        renderer = script.index("function buildScmReceiptSurfaceModel")
        self.assertLess(normalizer, renderer)

        self.assertNotIn("provenance.valid === true || hasProvenance", script)
        self.assertNotIn("studioState.lastScmReceiptVector,\n          findMcelLabReceiptPayload()", script)
        self.assertNotIn("PASS: wallet lifecycle is tamed by SCM", script)
        self.assertNotIn("eth_sendTransaction", script)
        self.assertNotIn("eth_signTransaction", script)
        self.assertNotIn("personal_sign", script)
        self.assertNotIn("signTypedData", script)
        self.assertNotRegex(script, r"\.sendTransaction\s*\(")
        self.assertIn("wallet21aPolicyBoundSendGate", script)
        self.assertIn("wallet21bProviderOutcomeLedger", script)
        self.assertIn("wallet21cTransactionWatcher", script)


    def test_code_studio_aider_control_owns_the_right_inspector(self) -> None:
        app = APP_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")
        contract = LAYOUT_CONTRACT_PATH.read_text(encoding="utf-8")

        inspector_start = app.index('<aside class="code-studio-inspector"')
        inspector_end = app.index("</aside>", inspector_start)
        proof_start = app.index('id="code-studio-bottom-panel"')
        receipt_start = app.index('id="code-studio-flagship-inspector"')
        doc_start = app.index('id="code-editor-doc-viewport"')
        history_start = app.index('id="aider-history-list"')

        self.assertLess(inspector_start, app.index('id="code-studio-aider-control"'))
        self.assertLess(app.index('id="aider-repo"'), inspector_end)
        self.assertLess(app.index('id="aider-files"'), inspector_end)
        self.assertLess(app.index('id="aider-instruction"'), inspector_end)
        self.assertLess(app.index('id="aider-preview"'), inspector_end)
        self.assertLess(app.index('id="aider-run"'), inspector_end)
        self.assertLess(app.index('id="aider-output"'), inspector_end)
        self.assertNotIn("SCM Receipt", app[inspector_start:inspector_end])

        self.assertLess(proof_start, receipt_start)
        self.assertLess(proof_start, doc_start)
        self.assertLess(proof_start, history_start)

        for element_id in [
            "aider-repo",
            "aider-files",
            "aider-instruction",
            "aider-preview",
            "aider-run",
            "aider-dry-run",
            "aider-output",
        ]:
            with self.subTest(element_id=element_id):
                self.assertEqual(app.count(f'id="{element_id}"'), 1)

        expected_style = [
            "Patch 19A: Aider owns the operational inspector",
            ".code-studio-aider-control",
            ".code-studio-aider-control-scroll",
            ".code-studio-aider-control-grid",
            ".code-studio-proof-workspace",
            ".code-studio-proof-receipts",
        ]
        for text in expected_style:
            with self.subTest(style=text):
                self.assertIn(text, style)

        self.assertIn('role: "agent-control"', contract)
        self.assertIn('role: "evidence-history"', contract)
        self.assertIn(
            '{subject: "code-editor.inspector", relation: "controls", object: "code-editor.editor", strength: "strong"}',
            contract,
        )
        self.assertIn(
            '{subject: "code-editor.proof", relation: "records", object: "aider.operation", strength: "strong"}',
            contract,
        )


    def test_code_studio_file_map_is_part_of_the_aider_control_surface(self) -> None:
        app = APP_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")
        contract = LAYOUT_CONTRACT_PATH.read_text(encoding="utf-8")

        sidebar_start = app.index('<aside class="code-studio-sidebar"')
        sidebar_end = app.index("</aside>", sidebar_start)
        inspector_start = app.index('<aside class="code-studio-inspector"')
        inspector_end = app.index("</aside>", inspector_start)
        repo_start = app.index('id="aider-repo"', inspector_start)
        map_start = app.index('class="code-studio-file-map-dock code-studio-aider-file-map"', inspector_start)
        selected_files_start = app.index('id="aider-files"', inspector_start)

        self.assertNotIn('id="file-map-list"', app[sidebar_start:sidebar_end])
        self.assertNotIn("Repo file map", app[sidebar_start:sidebar_end])
        self.assertLess(inspector_start, repo_start)
        self.assertLess(repo_start, map_start)
        self.assertLess(map_start, selected_files_start)
        self.assertLess(selected_files_start, inspector_end)

        for element_id in [
            "file-map-search",
            "file-map-refresh",
            "file-map-apply",
            "file-map-status",
            "file-map-list",
        ]:
            with self.subTest(element_id=element_id):
                self.assertEqual(app.count(f'id="{element_id}"'), 1)
                self.assertLess(app.index(f'id="{element_id}"'), inspector_end)

        self.assertIn('aria-label="Explorer"', app[sidebar_start:sidebar_end])
        self.assertIn("Repository context", app[inspector_start:inspector_end])
        self.assertIn("Choose files for Aider", app[inspector_start:inspector_end])
        self.assertIn(
            'data-mc-component-owner="code-editor.aider.workspace"',
            app[map_start:inspector_end],
        )
        self.assertIn(
            'placeholder="Mark files in the repository context above."',
            app[map_start:inspector_end],
        )

        expected_style = [
            "Patch 19B: the repository file map is part of the Aider control surface",
            ".code-studio-aider-file-map",
            "grid-template-rows: auto auto auto;",
            "max-block-size: none;",
            "overflow: visible;",
        ]
        for text in expected_style:
            with self.subTest(style=text):
                self.assertIn(text, style)

        self.assertIn('"code-editor.file-map": {', contract)
        self.assertIn('role: "agent-context-selector"', contract)
        self.assertIn(
            '{subject: "code-editor.file-map", relation: "selects", object: "workspace.selection", strength: "hard"}',
            contract,
        )
        self.assertIn(
            '{subject: "code-editor.file-map", relation: "feeds", object: "code-editor.inspector", strength: "strong"}',
            contract,
        )


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


    def test_code_editor_declares_live_mcel_dock_contract(self) -> None:
        app = APP_PATH.read_text(encoding="utf-8")
        expected = [
            'data-mc-layout-root="code-editor.workbench"',
            'data-mc-layout="dock-workbench"',
            'data-mc-layout-policy="editor-centered-workbench"',
            'data-mc-layout-user-id="code-editor.activity"',
            'data-mc-layout-user-id="code-editor.explorer"',
            'data-mc-layout-user-id="code-editor.editor"',
            'data-mc-layout-user-id="code-editor.inspector"',
            'data-mc-layout-user-id="code-editor.proof"',
            'data-mc-layout-user-id="code-editor.status"',
            'data-mc-layout-allowed="right bottom tab trigger"',
            'data-mc-layout-fallback="bottom tab trigger"',
            'data-mc-layout-user-mutable="placement share collapsed tab-group"',
            'id="code-editor-layout-menu"',
            'id="code-editor-layout-center-tabs"',
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, app)

        self.assertIn('data-mc-layout-strength="required"', app)
        self.assertIn('data-mc-authority="primary-work"', app)
        self.assertIn('data-mc-proves="editor.operation"', app)
        self.assertIn('data-mc-confirms="editor.state layout.state"', app)

    def test_code_editor_layout_contract_loads_before_studio_runtime(self) -> None:
        applications_html = APPLICATIONS_HTML.read_text(encoding="utf-8")
        include_order = [
            "applications/scripts/mcel-core.js",
            "applications/scripts/code-editor-layout-contract.js",
            "applications/scripts/code-editor-scm-manifest.js",
            "applications/scripts/code-editor-monaco-adapter.js",
            "applications/scripts/code-editor-mcel-studio.js",
        ]
        positions = [applications_html.index(include) for include in include_order]
        self.assertEqual(positions, sorted(positions))

    def test_code_editor_uses_semantic_layout_preferences_instead_of_grid_coordinates(self) -> None:
        contract = LAYOUT_CONTRACT_PATH.read_text(encoding="utf-8")
        studio = SCRIPT_PATH.read_text(encoding="utf-8")
        expected = [
            'const CONTRACT_VERSION = "mcel-code-editor-layout.v1"',
            'const STORAGE_KEY = "main-computer-code-editor-layout-preferences-v1"',
            '"editor-remains-center"',
            '"user-preferences-use-semantic-hints"',
            '"dock-workbench"',
            '"code-editor.inspector"',
            '"resize-share"',
            '"tab-with"',
            "rejectRawGeometry",
            "resolveLayout",
            "applyOperationToPreferences",
            "MainComputerCodeEditorLayout",
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, contract)

        self.assertNotIn("grid.save(false)", studio)
        self.assertNotIn("main-computer-code-editor-gridstack-layout-v1", studio)
        self.assertIn("layoutApi.mount(root)", studio)
        self.assertIn("window.applyCodeEditorLayoutOperation", studio)
        self.assertIn("window.saveCodeEditorGridStackLayout = () => controller.persist()", studio)

    def test_code_editor_live_dock_css_is_contract_driven(self) -> None:
        style = STYLE_PATH.read_text(encoding="utf-8")
        expected = [
            "MCEL Code Editor live dock-tree V1",
            '#code-editor-app[data-mcel-layout-live="true"]',
            "--mcel-code-editor-explorer-inline",
            "--mcel-code-editor-inspector-inline",
            "--mcel-code-editor-proof-block",
            'data-mcel-inspector-placement="bottom"',
            'data-mcel-inspector-placement="tab"',
            'data-mcel-explorer-placement="trigger"',
            'data-code-editor-layout-splitter="explorer"',
            'data-code-editor-layout-splitter="inspector"',
            'data-code-editor-layout-splitter="proof"',
            'grid-template-areas:',
            '"proof"',
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, style)

        self.assertEqual(style.count("{"), style.count("}"))

    def test_code_editor_runtime_generated_layout_is_contract_contained(self) -> None:
        app = APP_PATH.read_text(encoding="utf-8")
        style = STYLE_PATH.read_text(encoding="utf-8")
        studio = SCRIPT_PATH.read_text(encoding="utf-8")
        contract = LAYOUT_CONTRACT_PATH.read_text(encoding="utf-8")

        expected_markup = [
            'data-mc-layout-fill="owned-center-slot"',
            'data-mc-layout-tracks="content remaining"',
            'data-mc-layout-fill="remaining"',
            'data-mc-layout-overflow="contain"',
            'data-mc-layout-containment="owned-remaining-track"',
        ]
        for text in expected_markup:
            with self.subTest(markup=text):
                self.assertIn(text, app)

        expected_contract = [
            'const GENERATED_LAYOUT_CONTRACT = deepFreeze({',
            '"mcel-owned-track-containment.v1"',
            '"owned-remaining-track-descendants-contain-their-paint"',
            '"data-mcel-layout-node": "runtime-draft"',
            '"data-mcel-layout-containment": "paint-contained"',
            "function applyGeneratedLayoutContract",
            "applyGeneratedLayoutContract,",
        ]
        for text in expected_contract:
            with self.subTest(contract=text):
                self.assertIn(text, contract)

        self.assertIn("applyGeneratedLayoutContract?.(runtimePreview)", studio)
        self.assertIn("runtimePreview.dataset.mcelGeneratedLayoutContract", studio)

        expected_style = [
            "MCEL Code Editor owned remaining-track containment V1",
            '[data-mcel-layout-node="runtime-window"]',
            "grid-template-rows: auto minmax(0, 1fr);",
            '[data-mcel-layout-node="runtime-draft"]',
            "min-block-size: 0 !important;",
            "max-block-size: 100%;",
            "resize: none !important;",
            '[data-mcel-layout-capacity="compact"]',
        ]
        for text in expected_style:
            with self.subTest(style=text):
                self.assertIn(text, style)

        self.assertEqual(style.count("{"), style.count("}"))

    def test_code_editor_generated_layout_contract_applies_without_raw_geometry(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")

        script_literal = json.dumps(str(LAYOUT_CONTRACT_PATH))
        probe = f"""
global.window = {{}};
require({script_literal});
const api = window.MainComputerCodeEditorLayout;

function makeNode(selector) {{
  const attributes = {{}};
  return {{
    selector,
    attributes,
    setAttribute(name, value) {{ attributes[name] = String(value); }},
  }};
}}

const nodes = api.GENERATED_LAYOUT_CONTRACT.rules.map((rule) => makeNode(rule.selector));
const root = {{
  matches() {{ return false; }},
  querySelectorAll(selector) {{ return nodes.filter((node) => node.selector === selector); }},
}};
const result = api.applyGeneratedLayoutContract(root);
const draft = nodes.find((node) => node.selector === "#code-studio-runtime-draft");
console.log(JSON.stringify({{
  result,
  draft: draft.attributes,
  rawKeys: Object.keys(draft.attributes).filter((key) => /(?:left|top|width|height|x|y)$/i.test(key)),
}}));
"""
        completed = subprocess.run(
            [node, "-e", probe],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["result"]["complete"])
        self.assertEqual(payload["result"]["missing"], [])
        self.assertEqual(payload["draft"]["data-mcel-layout-fill"], "parent")
        self.assertEqual(payload["draft"]["data-mcel-layout-overflow"], "scroll")
        self.assertEqual(payload["draft"]["data-mcel-layout-containment"], "paint-contained")
        self.assertEqual(payload["rawKeys"], [])

    def test_code_editor_layout_resolver_remediates_without_forgetting_preference(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")

        script_literal = json.dumps(str(LAYOUT_CONTRACT_PATH))
        probe = f"""
global.window = {{}};
require({script_literal});
const api = window.MainComputerCodeEditorLayout;
const authored = {{
  complete: true,
  missing: [],
  mismatches: [],
  units: JSON.parse(JSON.stringify(api.SAFE_DEFAULTS))
}};
const preferences = JSON.parse(JSON.stringify(api.DEFAULT_PREFERENCES));
const wide = api.resolveLayout({{
  viewport: {{width: 1600, height: 900}},
  authored,
  preferences,
  proofExpanded: false
}});
const medium = api.resolveLayout({{
  viewport: {{width: 1024, height: 720}},
  authored,
  preferences,
  proofExpanded: false
}});
const compact = api.resolveLayout({{
  viewport: {{width: 520, height: 600}},
  authored,
  preferences,
  proofExpanded: false
}});
const raw = api.normalizePreferences({{
  units: {{
    "code-editor.inspector": {{
      placement: "right",
      preferredShare: 0.24,
      left: 300,
      width: 400
    }}
  }}
}}, authored);
const hidden = api.applyOperationToPreferences(
  preferences,
  {{kind: "dock", userId: "code-editor.inspector", placement: "trigger"}},
  authored
);
const reopened = api.applyOperationToPreferences(
  hidden.preferences,
  {{kind: "collapse", userId: "code-editor.inspector", collapsed: false}},
  authored
);
const restored = api.resolveLayout({{
  viewport: {{width: 1600, height: 900}},
  authored,
  preferences: reopened.preferences,
  proofExpanded: false
}});
console.log(JSON.stringify({{wide, medium, compact, raw, hidden, reopened, restored}}));
"""
        completed = subprocess.run(
            [node, "-e", probe],
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)

        self.assertEqual(result["wide"]["actual"]["explorer"], "left")
        self.assertEqual(result["wide"]["actual"]["inspector"], "right")
        self.assertEqual(result["wide"]["actual"]["proof"], "bottom")

        self.assertEqual(result["medium"]["preferred"]["inspector"], "right")
        self.assertEqual(result["medium"]["actual"]["inspector"], "bottom")
        self.assertTrue(result["medium"]["remediated"])

        self.assertEqual(result["compact"]["preferred"]["inspector"], "right")
        self.assertIn(result["compact"]["actual"]["inspector"], {"tab", "trigger"})
        self.assertIn(result["compact"]["capacity"], {"narrow", "compact"})

        violations = result["raw"]["rawGeometryViolations"]
        self.assertIn("preferences.units.code-editor.inspector.left", violations)
        self.assertIn("preferences.units.code-editor.inspector.width", violations)

        hidden_inspector = result["hidden"]["preferences"]["units"]["code-editor.inspector"]
        self.assertEqual(hidden_inspector["placement"], "right")
        self.assertTrue(hidden_inspector["collapsed"])
        self.assertFalse(result["reopened"]["preferences"]["units"]["code-editor.inspector"]["collapsed"])
        self.assertEqual(result["restored"]["actual"]["inspector"], "right")

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
