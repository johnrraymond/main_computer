from __future__ import annotations

from pathlib import Path

from main_computer.viewport_state import _application_route_target


ROOT = Path(__file__).resolve().parents[1]
WEB_APP = ROOT / "main_computer" / "web" / "applications"


def test_mcel_lab_is_registered_as_separate_application() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    navigation = (WEB_APP / "scripts" / "dom-bindings" / "navigation.js").read_text(encoding="utf-8")
    dom_bindings = (WEB_APP / "scripts" / "dom-bindings.js").read_text(encoding="utf-8")
    routing = (WEB_APP / "scripts" / "app-routing.js").read_text(encoding="utf-8")

    assert 'href="/applications/mcel-lab" data-app="mcel-lab"' in html
    assert "<!-- @include applications/apps/mcel-lab.html -->" in html
    assert "<!-- @include applications/styles/mcel-lab.css -->" in html
    assert "<!-- @include applications/scripts/mcel-contract.js -->" in html
    assert "<!-- @include applications/scripts/mcel-engine.js -->" in html
    assert "<!-- @include applications/scripts/mcel-law-registry.js -->" in html
    assert "<!-- @include applications/scripts/mcel-editor.js -->" in html
    assert "<!-- @include applications/scripts/mcel-style-law.js -->" in html
    assert "<!-- @include applications/scripts/mcel-chrome-law.js -->" in html
    assert "<!-- @include applications/scripts/mcel-browser-observer.js -->" in html
    assert "<!-- @include applications/scripts/mcel-layout-law.js -->" in html
    assert "<!-- @include applications/scripts/mcel-command-surface.js -->" in html
    assert "<!-- @include applications/scripts/mcel-project-store.js -->" in html
    assert "<!-- @include applications/scripts/mcel-scenarios.js -->" in html
    assert "<!-- @include applications/scripts/mcel-graph.js -->" in html
    assert "<!-- @include applications/scripts/mcel-ops-runner.js -->" in html
    assert "<!-- @include applications/scripts/mcel-acid-tests.js -->" in html
    assert "<!-- @include applications/scripts/mcel-test-harness.js -->" in html
    assert "<!-- @include applications/scripts/mcel-supervisor.js -->" in html
    assert "<!-- @include applications/scripts/mcel-kernel.js -->" in html
    assert "<!-- @include applications/scripts/mcel-core.js -->" in html
    assert "<!-- @include applications/scripts/task-manager-mcel.js -->" in html
    assert "<!-- @include applications/scripts/mcel-supercut.js -->" in html
    assert "<!-- @include applications/scripts/git-tools-mcel.js -->" in html
    assert "<!-- @include applications/scripts/mcel-lab.js -->" in html
    assert (
        html.index("mcel-contract.js")
        < html.index("mcel-engine.js")
        < html.index("mcel-law-registry.js")
        < html.index("mcel-editor.js")
        < html.index("mcel-style-law.js")
        < html.index("mcel-chrome-law.js")
        < html.index("mcel-browser-observer.js")
        < html.index("mcel-layout-law.js")
        < html.index("mcel-command-surface.js")
        < html.index("mcel-project-store.js")
        < html.index("mcel-scenarios.js")
        < html.index("mcel-graph.js")
        < html.index("mcel-ops-runner.js")
        < html.index("mcel-acid-tests.js")
        < html.index("mcel-test-harness.js")
        < html.index("mcel-supervisor.js")
        < html.index("mcel-kernel.js")
        < html.index("mcel-core.js")
        < html.index("task-manager-mcel.js")
        < html.index("mcel-supercut.js")
        < html.index("git-tools-mcel.js")
        < html.index("mcel-lab.js")
    )
    assert "<!-- @include applications/scripts/dom-bindings/mcel-lab.js -->" in dom_bindings
    assert '"mcel-lab": ["MCEL Lab"' in navigation
    assert '"web-test-bed": "mcel-lab"' in navigation
    assert "initMcelLabApp()" in routing
    assert "mcelLabApp.style.display = isMcelLab" in routing


def test_mcel_lab_route_targets_applications_shell() -> None:
    assert _application_route_target("/applications/mcel-lab") == "mcel-lab"
    assert _application_route_target("/applications/web-test-bed") == "mcel-lab"


def test_mcel_lab_mounts_task_manager_as_canonical_specimen() -> None:
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    lab = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    adapter = (WEB_APP / "scripts" / "task-manager-mcel.js").read_text(encoding="utf-8")
    css = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert 'id="mcel-canonical-app-frame"' in app
    assert 'data-mcel-specimen-app="task-manager"' in app
    assert 'data-mcel-specimen-root="#task-manager-app"' in app
    assert 'data-route="/applications/task-manager/server-processes?mcel_lab_specimen=task-manager"' in app
    assert 'sandbox="allow-same-origin allow-scripts allow-forms"' in app
    assert 'id="mcel-canonical-app-mount"' in app
    assert 'id="mcel-canonical-app-inspect"' in app
    assert 'id="mcel-canonical-app-proof"' in app
    assert 'id="mcel-canonical-app-enrich"' in app
    assert 'id="mcel-canonical-app-lens"' in app
    assert 'id="mcel-canonical-app-clean"' in app
    assert 'id="mcel-canonical-app-lens-map"' in app
    assert "mcelCanonicalAppFrame = document.querySelector" in bindings
    assert "lastCanonicalSpecimenReport" in bindings
    assert "canonicalAppSpecimen" in bindings
    assert "mountMcelCanonicalAppSpecimen" in lab
    assert "inspectMcelCanonicalAppSpecimen" in lab
    assert "runMcelCanonicalAppSpecimenProof" in lab
    assert "MCEL_CANONICAL_TASK_MANAGER_REQUIRED_IDS" in lab
    assert "MCEL_CANONICAL_TASK_MANAGER_DANGEROUS_CONTROL_SELECTORS" in lab
    assert ".mcel-canonical-specimen" in css
    assert ".mcel-canonical-specimen-frame-bar" in css
    assert ".mcel-canonical-specimen-badge" in css
    assert ".mcel-canonical-app-frame" in css
    assert ".mcel-canonical-app-report" in css
    assert ".mcel-canonical-app-lens-map" in css
    assert ".mcel-canonical-app-lens-map-card" in css
    assert 'id="mcel-canonical-app-frame-summary"' in app
    assert "mcelCanonicalAppFrameSummary = document.querySelector" in bindings
    assert "mcelCanonicalAppEnrich = document.querySelector" in bindings
    assert "mcelCanonicalAppLens = document.querySelector" in bindings
    assert "mcelCanonicalAppClean = document.querySelector" in bindings
    assert "mcelCanonicalAppLensMap = document.querySelector" in bindings
    assert "lastCanonicalSpecimenLens" in bindings
    assert "lastCanonicalSpecimenEnrichment" in bindings
    assert "injectMcelCanonicalAppSpecimenChrome" in lab
    assert "applyMcelCanonicalTaskManagerLens" in lab
    assert "applyMcelCanonicalTaskManagerEnrichment" in lab
    assert "mcelTaskManagerMcelAdapter" in lab
    assert "applyTaskManagerMcelSemantics" in lab
    assert "TaskManagerMcel" in lab
    assert "REGION_ENRICHMENT" in adapter
    assert "COMPONENT_ENRICHMENT" in adapter
    assert "FIELD_ENRICHMENT" in adapter
    assert "PANEL_LENS" in adapter
    assert "ACTION_LENS" in adapter
    assert "MCEL_CANONICAL_SPECIMEN_LENS_STYLE_ID" in lab
    assert "ENRICHMENT_STYLE_ID" in adapter
    assert "data-mcel-task-enrichment" in adapter
    assert "data-mcel-fit" in adapter
    assert "data-mcel-region" in adapter
    assert "mcel-lens-hud" in lab
    assert "data-mcel-action-risk" in lab
    assert "MCEL_CANONICAL_SPECIMEN_RIBBON_ID" in lab
    assert "fixed in-frame ribbon" in lab
    assert "clearMcelCanonicalTaskManagerLens" in lab


def test_mcel_lab_mounts_git_tools_as_busy_canonical_specimen() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    lab = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    adapter = (WEB_APP / "scripts" / "git-tools-mcel.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-supercut.js -->" in html
    assert "<!-- @include applications/scripts/git-tools-mcel.js -->" in html
    assert html.index("git-tools.js") < html.index("mcel-supercut.js") < html.index("git-tools-mcel.js") < html.index("mcel-lab.js")
    assert 'value="git-tools"' in app
    assert 'data-route="/applications/git-tools?mcel_lab_specimen=git-tools"' in app
    assert 'data-root="#git-tools-app"' in app
    assert "remote, mirror, push, or manual command actions" in app

    assert "window.GitToolsMcel" in lab
    assert "window.McelSupercut" in lab
    assert "MCEL_CANONICAL_GIT_TOOLS_REQUIRED_IDS" in lab
    assert "MCEL_CANONICAL_GIT_TOOLS_DANGEROUS_CONTROL_SELECTORS" in lab
    assert "mcelCanonicalAppAdapter" in lab
    assert "applyGitToolsMcelSemantics" in lab
    assert "data-mcel-git-enrichment" in lab
    assert "MCEL adapter unavailable" in lab
    assert "root.querySelectorAll(\".app-widget, .git-tools-card, .gitea-workflow-card" in lab

    assert "global.GitToolsMcel" in adapter
    assert "BODY_ENRICHMENT_ATTRIBUTE = \"data-mcel-git-enrichment\"" in adapter
    assert "#git-tools-app" in adapter
    assert "#git-server-remote-run" in adapter
    assert "command-execution" in adapter
    assert "credential-network-mutation" in adapter
    assert "applyGitToolsMcelSemantics" in adapter
    assert "runGitToolsSupercutTranslation" in adapter
    assert "supercutOriginalPoints" in adapter
    assert "htmlTranslationTool" in adapter
    assert "push, or manual command buttons" in adapter



def test_mcel_lab_adds_supercut_html_translation_module_for_git_tools() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    lab = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    adapter = (WEB_APP / "scripts" / "git-tools-mcel.js").read_text(encoding="utf-8")
    supercut = (WEB_APP / "scripts" / "mcel-supercut.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-supercut.js -->" in html
    assert html.index("task-manager-mcel.js") < html.index("mcel-supercut.js") < html.index("git-tools-mcel.js")
    assert "MCEL Supercut" in app
    assert "mcel-supercut component contracts" in app

    assert "window.McelSupercut" in lab
    assert "supercut: ${enrichment.supercutComponentCount" in lab
    assert "MCEL Supercut original points" in lab

    assert "global.McelSupercut.translateRuntime" in adapter
    assert "clearGitToolsSupercutTranslation" in adapter
    assert "supercutRuntimeChanges" in adapter
    assert "supercutCssObjectCount" in adapter
    assert "MCEL Supercut derives original points from slimy HTML structure" in adapter

    assert "global.McelSupercut" in supercut
    assert "inspectHtmlRuntime" in supercut
    assert "translateRuntime" in supercut
    assert "clearRuntime" in supercut
    assert "executeComponent" in supercut
    assert "data-mcel-supercut" in supercut
    assert "data-mcel-supercut-purpose" in supercut
    assert "data-mcel-supercut-executable" in supercut
    assert "htmlTranslationTool" in supercut
    assert "rectificationRounds" in supercut
    assert "cssObjectCatalog" in supercut
    assert "runtimeChanges" in supercut
    assert "originalPoint" in supercut


def test_mcel_supercut_risk_outlines_require_explicit_debug_overlay() -> None:
    supercut = (WEB_APP / "scripts" / "mcel-supercut.js").read_text(encoding="utf-8")

    assert 'const SUPERCUT_DEBUG_ATTRIBUTE = "data-mcel-supercut-debug";' in supercut
    assert 'options.debugOverlay === true || options.debug === true || options.showRiskOutlines === true' in supercut
    assert 'body[${SUPERCUT_BODY_ATTRIBUTE}="active"][${SUPERCUT_DEBUG_ATTRIBUTE}="true"] [data-mcel-supercut-risk]:not([data-mcel-supercut-risk="safe"])' in supercut
    assert 'body[${SUPERCUT_BODY_ATTRIBUTE}="active"] [data-mcel-supercut-risk]:not([data-mcel-supercut-risk="safe"])' not in supercut
    assert 'doc.body.removeAttribute(SUPERCUT_DEBUG_ATTRIBUTE);' in supercut


def test_mcel_lab_task_manager_specimen_route_is_valid_and_observational() -> None:
    assert (
        _application_route_target("/applications/task-manager/server-processes?mcel_lab_specimen=task-manager")
        == "task-manager"
    )

    lab = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    specimen_block = lab[
        lab.index("const MCEL_CANONICAL_TASK_MANAGER_REQUIRED_IDS"):
        lab.index("function openMcelDiagnosticsDrawer")
    ]

    assert '"task-manager-app"' in specimen_block
    assert '"task-all-process-table"' in specimen_block
    assert '"task-connection-table"' in specimen_block
    assert '"task-server-shutdown"' in specimen_block
    assert '"task-server-restart"' in specimen_block
    assert '"[data-task-action=\\"terminate-pid\\"]"' in specimen_block
    assert '"[data-task-action=\\"kill-pid\\"]"' in specimen_block
    assert "specimenChromeApplied" in specimen_block
    assert "lensActive" in specimen_block
    assert "lensPanelCount" in specimen_block
    assert "lensRiskControlCount" in specimen_block
    assert "canonical lens annotates Task Manager and reports risk in the Lab sidecar" in specimen_block
    assert "data-mcel-lab-specimen-root" in specimen_block
    assert "destructiveActionsExecuted: false" in specimen_block
    assert "does not click server control" in specimen_block
    assert ".click(" not in specimen_block


def test_mcel_lab_task_manager_lens_is_lab_only_and_risk_aware() -> None:
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    lab = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    adapter = (WEB_APP / "scripts" / "task-manager-mcel.js").read_text(encoding="utf-8")
    css = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")
    specimen_block = lab[
        lab.index("const MCEL_CANONICAL_TASK_MANAGER_REQUIRED_IDS"):
        lab.index("function openMcelDiagnosticsDrawer")
    ]

    assert "Inspect MCEL Lens" in app
    assert "Clean Specimen" in app
    assert "Task Manager enrichment map" in app
    assert "renderMcelCanonicalAppLensMap" in specimen_block
    assert "applyMcelCanonicalTaskManagerLens" in specimen_block
    assert "data-mcel-canonical-lens" in specimen_block
    assert "data-mcel-action-risk" in specimen_block
    assert "process-destructive" in adapter
    assert "deferred-mutation" in adapter
    assert "lab-side inspector lens active" in specimen_block
    assert "lens application never clicks Task Manager controls" in specimen_block
    assert "no inline labels or risk badges" in specimen_block
    assert "does not restyle layout" in specimen_block
    assert '"task-all-processes-table"' not in specimen_block
    assert '"task-connections-table"' not in specimen_block
    assert ".mcel-canonical-app-lens-map-grid" in css
    assert ".mcel-canonical-app-lens-sidecar-list" in css


def test_mcel_lab_task_manager_enrichment_is_role_based_before_render() -> None:
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    lab = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    adapter = (WEB_APP / "scripts" / "task-manager-mcel.js").read_text(encoding="utf-8")
    specimen_block = lab[
        lab.index("const MCEL_CANONICAL_TASK_MANAGER_REQUIRED_IDS"):
        lab.index("function openMcelDiagnosticsDrawer")
    ]
    enrichment_style = adapter[
        adapter.index("function ensureEnrichmentStyle"):
        adapter.index("function applyElementEnrichment")
    ]

    assert "Enrich MCEL Model" in app
    assert "reads the legacy DOM into a MCEL enrichment model" in app
    assert "regions, components, fields, actions, and fit laws" in specimen_block
    assert "structural containers preserve app geometry" in adapter
    assert "constrained regions use compact leaf-control fit policies" in adapter
    assert "checkbox controls reserve a fixed input slot and shrinkable label slot" in adapter
    assert "data-mcel-layout-region" in specimen_block
    assert "[data-mcel-region=\"command-status-rail\"]" in enrichment_style
    assert "[data-mcel-fit=\"compact-controls\"]" in enrichment_style
    assert "[data-mcel-fit=\"fixed-input-shrink-label\"]" in enrichment_style
    assert "[data-mcel-control-priority=\"primary\"]" in enrichment_style
    assert "#task-include-connections" not in enrichment_style
    assert "connections" not in enrichment_style.lower()
    assert "data-mcel-action-risk" in specimen_block
    assert "destructiveActionsExecuted: false" in specimen_block
    assert ".click(" not in specimen_block


def test_mcel_task_manager_notebook_uses_canonical_tabbed_workspace() -> None:
    task_html = (WEB_APP / "apps" / "task-manager.html").read_text(encoding="utf-8")
    adapter = (WEB_APP / "scripts" / "task-manager-mcel.js").read_text(encoding="utf-8")
    elements = (WEB_APP / "scripts" / "mcel-elements-core.js").read_text(encoding="utf-8")
    toolkit = (WEB_APP / "scripts" / "mcel-toolkit-core.js").read_text(encoding="utf-8")
    concerns = (WEB_APP / "scripts" / "mcel-concern-core.js").read_text(encoding="utf-8")

    assert 'role="tablist"' in task_html
    assert 'role="tab"' in task_html
    assert 'role="tabpanel"' in task_html
    assert "setTaskNotebookTab" in (WEB_APP / "scripts" / "task-manager.js").read_text(encoding="utf-8")

    for element_id in [
        "element.toolkit.tabbed-workspace",
        "element.toolkit.tab-list",
        "element.toolkit.tab",
        "element.toolkit.tab-panel",
        "element.toolkit.tab-controller",
    ]:
        assert element_id in elements
        assert element_id in adapter

    assert "tabs-are-view-state-not-command-buttons" in elements
    assert "buttons pretending to be tabs" in elements
    assert "pattern.tabbed-workspace" in toolkit
    assert "layout.tabbed-workspace" in toolkit
    assert "controller.tab-state" in toolkit
    assert "tabbed-data-workspace" in toolkit
    assert "button-row-as-navigation" in toolkit

    assert "concern.tabbed-workspace" in concerns
    assert "task-manager.data-notebook" in concerns
    assert "tabbedWorkspaceDetected" in concerns

    assert "TABBED_WORKSPACE_CONTRACT" in adapter
    assert "buildTabbedWorkspaceModel" in adapter
    assert "applyTabbedWorkspaceSemantics" in adapter
    assert 'role: "workspace-tab"' in adapter
    assert 'data-mcel-action-role", "switch-tab-view-state"' in adapter
    assert 'data-mcel-action-risk", "safe"' in adapter
    assert 'data-mcel-mutates", "false"' in adapter
    assert "tabbedWorkspaceContractStatus" in adapter
    assert "tabbedWorkspaceActiveTab" in adapter
    assert 'role: "feed-tab"' not in adapter
    assert "tabbed-data-feed" not in adapter


def test_task_manager_notebook_tabs_have_visual_tab_affordance() -> None:
    css = (WEB_APP / "styles" / "task-manager.css").read_text(encoding="utf-8")

    tab_button_block = css.split(".task-tab-button {", 1)[1].split("}", 1)[0]
    active_tab_block = css.split('.task-tab-button[aria-selected="true"] {', 1)[1].split("}", 1)[0]
    panels_block = css.split(".task-tab-panels {", 1)[1].split("}", 1)[0]

    assert ".task-pane.task-notebook" in css
    assert "grid-template-rows: auto minmax(0, 1fr)" in css
    assert "border-radius: 999px" not in tab_button_block
    assert "border-radius: 10px 10px 0 0" in tab_button_block
    assert "border-bottom-color: rgba(246, 199, 91, 0.48)" in tab_button_block
    assert "border-bottom-color: #010201" in active_tab_block
    assert ".task-tab-button.active::after" in css
    assert 'task-tab-button[aria-selected="true"]::after' in css
    assert "border-radius: 0 10px 10px 10px" in panels_block
    assert ".task-notebook .task-grid-scroll" in css
    assert "background: transparent" in css


def test_mcel_lab_assets_define_round_trip_contract() -> None:
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    contract = (WEB_APP / "scripts" / "mcel-contract.js").read_text(encoding="utf-8")
    engine = (WEB_APP / "scripts" / "mcel-engine.js").read_text(encoding="utf-8")
    editor = (WEB_APP / "scripts" / "mcel-editor.js").read_text(encoding="utf-8")
    style_law = (WEB_APP / "scripts" / "mcel-style-law.js").read_text(encoding="utf-8")
    law_registry = (WEB_APP / "scripts" / "mcel-law-registry.js").read_text(encoding="utf-8")
    browser_observer = (WEB_APP / "scripts" / "mcel-browser-observer.js").read_text(encoding="utf-8")
    layout_law = (WEB_APP / "scripts" / "mcel-layout-law.js").read_text(encoding="utf-8")
    core = (WEB_APP / "scripts" / "mcel-core.js").read_text(encoding="utf-8")
    command_surface = (WEB_APP / "scripts" / "mcel-command-surface.js").read_text(encoding="utf-8")
    project_store = (WEB_APP / "scripts" / "mcel-project-store.js").read_text(encoding="utf-8")
    graph = (WEB_APP / "scripts" / "mcel-graph.js").read_text(encoding="utf-8")
    ops_runner = (WEB_APP / "scripts" / "mcel-ops-runner.js").read_text(encoding="utf-8")
    acid = (WEB_APP / "scripts" / "mcel-acid-tests.js").read_text(encoding="utf-8")
    scenarios = (WEB_APP / "scripts" / "mcel-scenarios.js").read_text(encoding="utf-8")
    graph = (WEB_APP / "scripts" / "mcel-graph.js").read_text(encoding="utf-8")
    harness = (WEB_APP / "scripts" / "mcel-test-harness.js").read_text(encoding="utf-8")
    supervisor = (WEB_APP / "scripts" / "mcel-supervisor.js").read_text(encoding="utf-8")
    kernel = (WEB_APP / "scripts" / "mcel-kernel.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    style = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert "mcel-source-html" in app
    assert "mcel-runtime-preview" in app
    assert "mcel-serializer-diff" in app
    assert "mcel-debugger-output" in app
    assert "mcel-test-report" in app
    assert "mcel-scenario-select" in app
    assert "mcel-selection-status" in app
    assert "mcel-theme-select" in app
    assert "mcel-command-input" in app
    assert "mcel-css-law-report" in app
    assert "mcel-layout-law-report" in app
    assert "mcel-trait-overflow-policy" in app
    assert "mcel-project-report" in app
    assert "mcel-graph-report" in app
    assert "mcel-audit-report" in app
    assert "mcel-matrix-report" in app
    assert "mcel-evidence-report" in app
    assert "mcel-supervisor-report" in app
    assert "mcel-kernel-report" in app
    assert "mcel-traceability-report" in app
    assert "mcel-prior-art-report" in app
    assert "mcel-readiness-score" in app
    assert "Run Operational Audit" in app
    assert "Run Scenario Matrix" in app
    assert "Build Evidence Packet" in app
    assert "Run Kernel Audit" in app
    assert "Build Traceability Map" in app
    assert "Apply traits to selected widget" in app
    assert "data-mcel-mode=\"stress\"" in app
    assert "data-mc-generated" in contract
    assert "runtimeOwnedAttributes" in contract
    assert "serializeRuntimeRoot" in engine
    assert "repairRuntimeRoot" in engine
    assert "computedNeighborhood" in engine
    assert "computeA11y" in engine
    assert "runContractTests" in engine
    assert "sourceIndex" in contract
    assert "McelLabEditor" in editor
    assert "sanitizeEditorHtml" in editor
    assert "applyTraits" in editor
    assert "insertBlock" in editor
    assert "McelLabStyleLaw" in style_law
    assert "applyRuntimeLaw" in style_law
    assert "McelLabLawRegistry" in law_registry
    assert "McelLabBrowserObserver" in browser_observer
    assert "McelLabLayoutLaw" in layout_law
    assert "window.MCEL = MCEL" in core
    assert "layout law proves overflow and scrollbar policy without source pollution" in harness
    assert "public MCEL core API fronts compile/serialize/repair/audit/inspect" in harness
    assert "McelLabCommandSurface" in command_surface
    assert "McelLabCommandSurface" in command_surface and "Semantic Command" in app
    assert "McelLabProjectStore" in project_store
    assert "never generated runtime DOM" in project_store
    assert "McelLabScenarios" in scenarios
    assert "McelLabGraph" in graph
    assert "McelLabOpsRunner" in ops_runner
    assert "McelLabAcidTests" in acid
    assert "acidRuntimePollutionFirewall" in acid
    assert "runScenarioMatrix" in ops_runner
    assert "buildEvidencePacket" in ops_runner
    assert "buildReadiness" in ops_runner
    assert "graphFromSource" in graph
    assert "operational-audit" in graph
    assert "Neighborhood Cluster" in scenarios
    assert "Relation Hooks" in scenarios
    assert "McelLabTestHarness" in harness
    assert "acid tests survive hostile runtime/editor/serializer pressure" in harness
    assert "McelLabSupervisor" in supervisor
    assert "McelLabKernel" in kernel
    assert "buildTraceabilityMap" in kernel
    assert "runKernelAudit" in kernel
    assert "priorArtMatrix" in kernel
    assert "runFullProof" in supervisor
    assert "buildQualityGate" in supervisor
    assert "editor save firewall strips generated runtime DOM" in harness
    assert "runMcelContractTests" in ui
    assert "runMcelScenarioMatrix" in ui
    assert "runSelectedMcelAcidTest" in ui
    assert "runMcelAcidTests" in ui
    assert "buildMcelEvidencePacket" in ui
    assert "renderMcelReadiness" in ui
    assert "applyMcelTraitsToSelectedSourceWidget" in ui
    assert "selectMcelSourceIndex" in ui
    assert "GrapesJS is unavailable; semantic block insertion and trait editing remain active." in app
    assert "source HTML -> runtime DOM -> serializer round trips" in style


def test_mcel_lab_themes_are_product_grade_and_reach_the_iframe() -> None:
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    contract = (WEB_APP / "scripts" / "mcel-contract.js").read_text(encoding="utf-8")
    style_law = (WEB_APP / "scripts" / "mcel-style-law.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    style = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")

    real_themes = {
        "theme-machine": "Original MCEL",
        "theme-local": "Local Service",
        "theme-saas": "SaaS Launch",
        "theme-editorial": "Editorial / Magazine",
        "theme-luxury": "Luxury / Portfolio",
        "theme-civic": "Civic / Nonprofit",
        "theme-accessible": "Accessible High Contrast",
        "theme-debug": "Debug Wireframe",
    }

    for theme_id, label in real_themes.items():
        assert f'"{theme_id}"' in contract
        assert f'value="{theme_id}"' in app
        assert label in app
        assert f'.mcel-runtime-preview.{theme_id}' in style
        assert f'body.{theme_id}' in ui

    assert 'theme: "theme-machine"' in bindings
    assert 'class="mcel-runtime-preview theme-machine"' in app
    assert "themeCatalog" in style_law
    assert "themeDefinition" in style_law
    assert "themeLabel" in style_law
    assert "themeAliases" in contract and "machine: \"theme-machine\"" in contract
    assert "option.textContent = theme.label || theme.id" in ui
    assert 'data-mcel-theme="${theme}"' in ui
    assert 'body class="mcel-site-theme ${theme}"' in ui
    assert "--site-hero-ornament-bg" in ui
    assert "rgba(174,224,111,0.94)" in ui
    assert "--mcel-theme-canvas" in style
    assert "MCEL_THEME_CHANGED" in ui


def test_mcel_lab_chromes_select_structural_chrome_family() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    chrome_law = (WEB_APP / "scripts" / "mcel-chrome-law.js").read_text(encoding="utf-8")
    project_store = (WEB_APP / "scripts" / "mcel-project-store.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    style = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-chrome-law.js -->" in html
    assert html.index("mcel-style-law.js") < html.index("mcel-chrome-law.js") < html.index("mcel-browser-observer.js")
    assert "mcel-chrome-select" in app
    assert 'value="chrome-strict-hierarchy">Strict Hierarchy' in app
    assert 'value="chrome-editorial-flow">Editorial Flow' in app
    assert 'value="chrome-cluster-grid">Cluster Grid' in app
    assert 'value="chrome-spotlight">Spotlight' in app
    assert 'value="chrome-journey">Journey' in app
    assert 'value="chrome-compact-disclosure">Compact Disclosure' in app
    assert 'chrome: "chrome-strict-hierarchy"' in bindings
    assert "lastChromeReport" in bindings
    assert "mcelChromeSelect" in bindings
    assert "McelLabChromeLaw" in chrome_law
    assert 'CONTRACT_VERSION = "mcel.chrome.v1"' in chrome_law
    assert '"chrome-strict-hierarchy"' in chrome_law
    assert '"chrome-editorial-flow"' in chrome_law
    assert '"chrome-cluster-grid"' in chrome_law
    assert '"chrome-spotlight"' in chrome_law
    assert '"chrome-journey"' in chrome_law
    assert '"chrome-compact-disclosure"' in chrome_law
    assert '"cluster-grid.v1": "chrome-cluster-grid"' in chrome_law
    assert '"spotlight.v1": "chrome-spotlight"' in chrome_law
    assert '"journey.v1": "chrome-journey"' in chrome_law
    assert '"compact-disclosure.v1": "chrome-compact-disclosure"' in chrome_law
    assert "peer-cluster-render" in chrome_law
    assert "priority-render" in chrome_law
    assert "sequence-render" in chrome_law
    assert "disclosure-render" in chrome_law
    assert "preservesPixelBaseline: true" in chrome_law
    assert "return applyStrictHierarchyHtml" in chrome_law
    assert "changed: false" in chrome_law
    assert "CHROME_GENERATED_ATTR = \"data-mcel-chrome-generated\"" in chrome_law
    assert "CHROME_FRAME_ATTR = \"data-mcel-chrome-frame\"" in chrome_law
    assert "CHROME_REGION_ROLE_ATTR = \"data-mcel-chrome-region-role\"" in chrome_law
    assert "generatedObjectFrame" in chrome_law
    assert 'generatedRegion("compact-summary", chrome, "header", "summary")' in chrome_law
    assert 'generatedRegion("compact-body", chrome, "body")' in chrome_law
    assert "panel.appendChild(child)" not in chrome_law
    assert "MCEL.normalizeChrome(mcelLabState.chrome)" in ui
    assert "MCEL.applyChrome(runtimeHtml" in ui
    assert "changeMcelChrome" in ui
    assert "MCEL_CHROME_CHANGED" in ui
    assert 'data-mcel-chrome="${chrome}"' in ui
    assert 'body class="mcel-site-theme ${theme}" data-mcel-chrome="${chrome}"' in ui
    assert 'body[data-mcel-chrome="chrome-editorial-flow"]' in ui
    assert 'body[data-mcel-chrome="chrome-cluster-grid"]' in ui
    assert 'body[data-mcel-chrome="chrome-spotlight"]' in ui
    assert 'body[data-mcel-chrome="chrome-journey"]' in ui
    assert 'body[data-mcel-chrome="chrome-compact-disclosure"]' in ui
    assert "mcel-chrome-editorial-shell" in ui
    assert "mcel-chrome-cluster-grid" in ui
    assert "mcel-chrome-spotlight-primary" in ui
    assert "mcel-chrome-journey-step" in ui
    assert "mcel-chrome-compact-panel" in ui
    assert 'chrome: String(state.chrome || "chrome-strict-hierarchy")' in project_store
    assert "minmax(160px, 220px) minmax(160px, 220px)" in style


def test_mcel_lab_has_low_debt_module_boundaries() -> None:
    contract = (WEB_APP / "scripts" / "mcel-contract.js").read_text(encoding="utf-8")
    engine = (WEB_APP / "scripts" / "mcel-engine.js").read_text(encoding="utf-8")
    law_registry = (WEB_APP / "scripts" / "mcel-law-registry.js").read_text(encoding="utf-8")
    browser_observer = (WEB_APP / "scripts" / "mcel-browser-observer.js").read_text(encoding="utf-8")
    layout_law = (WEB_APP / "scripts" / "mcel-layout-law.js").read_text(encoding="utf-8")
    core = (WEB_APP / "scripts" / "mcel-core.js").read_text(encoding="utf-8")
    style_law = (WEB_APP / "scripts" / "mcel-style-law.js").read_text(encoding="utf-8")
    command_surface = (WEB_APP / "scripts" / "mcel-command-surface.js").read_text(encoding="utf-8")
    project_store = (WEB_APP / "scripts" / "mcel-project-store.js").read_text(encoding="utf-8")
    graph = (WEB_APP / "scripts" / "mcel-graph.js").read_text(encoding="utf-8")
    ops_runner = (WEB_APP / "scripts" / "mcel-ops-runner.js").read_text(encoding="utf-8")
    acid = (WEB_APP / "scripts" / "mcel-acid-tests.js").read_text(encoding="utf-8")
    supervisor = (WEB_APP / "scripts" / "mcel-supervisor.js").read_text(encoding="utf-8")
    kernel = (WEB_APP / "scripts" / "mcel-kernel.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")

    assert "var McelLabContract" in contract
    assert "var McelLabEngine" in engine
    assert "window.McelLabContract" in contract
    assert "window.McelLabEngine" in engine
    assert "const schema" in contract
    assert "const blockTemplates" in contract
    assert "const themes" in contract
    assert "schemaFor(" in engine
    assert "removeRuntimeState(" in engine
    assert "generatedPartsCanonical" in engine
    assert "mcelRunTests" in bindings
    assert "mcelTestReport" in bindings
    assert "mcelScenarioSelect" in bindings
    assert "mcelSelectionStatus" in bindings
    assert "mcelThemeSelect" in bindings
    assert "mcelCommandInput" in bindings
    assert "mcelProjectSave" in bindings
    assert "var McelLabStyleLaw" in style_law
    assert "var McelLabLawRegistry" in law_registry
    assert "var McelLabBrowserObserver" in browser_observer
    assert "var McelLabLayoutLaw" in layout_law
    assert "var MCEL" in core
    assert "var McelLabCommandSurface" in command_surface
    assert "var McelLabProjectStore" in project_store
    assert "var McelLabGraph" in graph
    assert "var McelLabOpsRunner" in ops_runner
    assert "var McelLabAcidTests" in acid
    assert "listCases" in acid
    assert "runOne" in acid
    assert "var McelLabSupervisor" in supervisor
    assert "var McelLabKernel" in kernel
    assert "requirementLedger" in kernel
    assert "moduleManifest" in kernel
    assert "compactEvidenceText" in ops_runner
    assert "compactReport" in graph
    assert "const mcelLabSchema" not in ui
    assert "function createMcelGeneratedPart" not in ui


def test_mcel_lab_third_slice_pushes_editor_contract_without_runtime_pollution() -> None:
    contract = (WEB_APP / "scripts" / "mcel-contract.js").read_text(encoding="utf-8")
    engine = (WEB_APP / "scripts" / "mcel-engine.js").read_text(encoding="utf-8")
    editor = (WEB_APP / "scripts" / "mcel-editor.js").read_text(encoding="utf-8")
    graph = (WEB_APP / "scripts" / "mcel-graph.js").read_text(encoding="utf-8")
    harness = (WEB_APP / "scripts" / "mcel-test-harness.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    style = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert 'sourceIndex: "data-mc-source-index"' in contract
    assert 'editorSelected: "data-mc-editor-selected"' in contract
    assert "element.setAttribute(attributes.sourceIndex, String(sourceIndex))" in engine
    assert "sourceElements(doc.body).map((element, index)" in editor
    assert "removeRuntimeState(element)" in editor
    assert "data-gjs-type" in editor
    assert "selection-aware traits update selected widget" in harness
    assert "relation hook resolves through semantic source" in harness
    assert "McelLabTestHarness.runAll()" in ui
    assert "selectedRuntimeElement()" in ui
    assert "markSelectedMcelRuntimeElement()" in ui
    assert ".mcel-lab-scenarios" in style
    assert '[data-mc-editor-selected="true"]' in style


def test_mcel_lab_fourth_slice_adds_operational_surface_without_source_debt() -> None:
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    contract = (WEB_APP / "scripts" / "mcel-contract.js").read_text(encoding="utf-8")
    style_law = (WEB_APP / "scripts" / "mcel-style-law.js").read_text(encoding="utf-8")
    command_surface = (WEB_APP / "scripts" / "mcel-command-surface.js").read_text(encoding="utf-8")
    project_store = (WEB_APP / "scripts" / "mcel-project-store.js").read_text(encoding="utf-8")
    graph = (WEB_APP / "scripts" / "mcel-graph.js").read_text(encoding="utf-8")
    graph = (WEB_APP / "scripts" / "mcel-graph.js").read_text(encoding="utf-8")
    harness = (WEB_APP / "scripts" / "mcel-test-harness.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    style = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert "Semantic Command" in app
    assert "Save Project" in app
    assert "CSS Law" in app
    assert 'theme: "data-mc-theme"' in contract
    assert 'flowAxis: "data-mc-flow-axis"' in contract
    assert "attributes.flowAxis" in contract
    assert "applyRuntimeLaw" in style_law
    assert "computeElementLaw" in style_law
    assert "McelLabCommandSurface" in command_surface
    assert "set-trait" in command_surface
    assert "insert-block" in command_surface
    assert "McelLabProjectStore" in project_store
    assert "main-computer.mcel-lab.project.v1" in project_store
    assert "CSS law publishes runtime tokens without source pollution" in harness
    assert "semantic command surface mutates clean source contracts" in harness
    assert "project snapshots persist clean semantic source only" in harness
    assert "applyMcelRuntimeStyleLaw" in ui
    assert "applyMcelSemanticCommand" in ui
    assert "saveMcelProject" in ui
    assert "theme-debug" in style
    assert "data-mc-style-law" in style


def test_mcel_lab_fifth_slice_adds_operational_graph_and_audit_provenance() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    contract = (WEB_APP / "scripts" / "mcel-contract.js").read_text(encoding="utf-8")
    engine = (WEB_APP / "scripts" / "mcel-engine.js").read_text(encoding="utf-8")
    graph = (WEB_APP / "scripts" / "mcel-graph.js").read_text(encoding="utf-8")
    command_surface = (WEB_APP / "scripts" / "mcel-command-surface.js").read_text(encoding="utf-8")
    harness = (WEB_APP / "scripts" / "mcel-test-harness.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    style = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-graph.js -->" in html
    assert html.index("mcel-scenarios.js") < html.index("mcel-graph.js") < html.index("mcel-ops-runner.js") < html.index("mcel-test-harness.js")
    assert "Run Operational Audit" in app
    assert "Semantic Graph" in app
    assert "Operational Audit" in app
    assert "mcelRunAudit" in bindings
    assert "mcelGraphReport" in bindings
    assert "mcelAuditReport" in bindings
    assert 'artifactOwner: "data-mc-owner"' in contract
    assert 'contractVersion = "mcel-lab.v0.11-ui-site-skeleton"' in contract
    assert "attributes.artifactOwner" in contract
    assert 'node.setAttribute(attributes.artifactOwner, "mcel-part-manager")' in engine
    assert 'node.setAttribute(attributes.artifactReason' in engine
    assert 'element.setAttribute(attributes.artifactOwner, "mcel-runtime-builder")' in engine
    assert "var McelLabGraph" in graph
    assert "graphFromRuntime" in graph
    assert "hasRuntimeAttributeLeakage" in graph
    assert "generated parts carry provenance" in graph
    assert 'audit: ["audit", "govern", "prove"]' in command_surface
    assert "semantic graph maps source/runtime nodes and generated parts" in harness
    assert "operational audit blocks source/runtime/provenance regressions" in harness
    assert "runMcelOperationalAudit" in ui
    assert "renderMcelGraphReport" in ui
    assert "renderMcelAuditReport" in ui
    assert 'data-mc-contract-version' in style


def test_mcel_lab_sixth_slice_adds_ci_like_scenario_matrix_and_evidence_packet() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    ops_runner = (WEB_APP / "scripts" / "mcel-ops-runner.js").read_text(encoding="utf-8")
    command_surface = (WEB_APP / "scripts" / "mcel-command-surface.js").read_text(encoding="utf-8")
    harness = (WEB_APP / "scripts" / "mcel-test-harness.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    style = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-ops-runner.js -->" in html
    assert html.index("mcel-graph.js") < html.index("mcel-ops-runner.js") < html.index("mcel-acid-tests.js") < html.index("mcel-test-harness.js") < html.index("mcel-supervisor.js")
    assert "Run Scenario Matrix" in app
    assert "Build Evidence Packet" in app
    assert "mcel-readiness-score" in app
    assert "mcel-readiness-cards" in app
    assert "Scenario Matrix" in app
    assert "Evidence Packet" in app
    assert "mcelRunMatrix" in bindings
    assert "mcelBuildEvidence" in bindings
    assert "mcelMatrixReport" in bindings
    assert "mcelEvidenceReport" in bindings
    assert "mcelReadinessCards" in bindings
    assert "var McelLabOpsRunner" in ops_runner
    assert "runScenarioMatrix" in ops_runner
    assert "mcel-scenario-theme-matrix" in ops_runner
    assert "buildEvidencePacket" in ops_runner
    assert "mcel-operational-evidence-packet" in ops_runner
    assert "buildReadiness" in ops_runner
    assert "serializedHasGeneratedMarkup" in ops_runner
    assert 'matrix: ["matrix", "coverage", "all-scenarios"]' in command_surface
    assert 'evidence: ["evidence", "packet"]' in command_surface
    assert "scenario-theme matrix proves cross-mode coverage" in harness
    assert "evidence packet summarizes operational readiness" in harness
    assert "runMcelScenarioMatrix" in ui
    assert "runSelectedMcelAcidTest" in ui
    assert "runMcelAcidTests" in ui
    assert "buildMcelEvidencePacket" in ui
    assert "renderMcelScenarioMatrix" in ui
    assert "renderMcelEvidencePacket" in ui
    assert "renderMcelReadiness" in ui
    assert ".mcel-lab-readiness" in style
    assert "#mcel-matrix-report" in style
    assert "#mcel-evidence-report" in style



def test_mcel_lab_boot_is_safe_in_concatenated_inline_script() -> None:
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    ops_runner = (WEB_APP / "scripts" / "mcel-ops-runner.js").read_text(encoding="utf-8")
    module_names = [
        "mcel-contract.js",
        "mcel-engine.js",
        "mcel-law-registry.js",
        "mcel-editor.js",
        "mcel-style-law.js",
        "mcel-browser-observer.js",
        "mcel-layout-law.js",
        "mcel-command-surface.js",
        "mcel-project-store.js",
        "mcel-scenarios.js",
        "mcel-graph.js",
        "mcel-ops-runner.js",
        "mcel-acid-tests.js",
        "mcel-test-harness.js",
        "mcel-supervisor.js",
        "mcel-kernel.js",
    ]

    assert "function createDefaultMcelLabState()" in bindings
    assert "var mcelLabState = window.mcelLabState" in bindings
    assert "function mcelLabDependenciesReady()" in ui
    assert "window.setTimeout(initMcelLabApp, 0)" in ui
    assert "var mcelLabState = window.mcelLabState" in ui
    assert "function testHarness()" in ops_runner
    assert 'const testHarness = typeof McelLabTestHarness' not in ops_runner
    for name in module_names:
        text = (WEB_APP / "scripts" / name).read_text(encoding="utf-8")
        assert "const McelLab" not in text
        assert "var McelLab" in text


def test_mcel_lab_seventh_slice_adds_autopilot_supervisor_quality_gate() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    command_surface = (WEB_APP / "scripts" / "mcel-command-surface.js").read_text(encoding="utf-8")
    supervisor = (WEB_APP / "scripts" / "mcel-supervisor.js").read_text(encoding="utf-8")
    kernel = (WEB_APP / "scripts" / "mcel-kernel.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-supervisor.js -->" in html
    assert "<!-- @include applications/scripts/mcel-kernel.js -->" in html
    assert html.index("mcel-test-harness.js") < html.index("mcel-supervisor.js") < html.index("mcel-kernel.js") < html.index("mcel-core.js") < html.index("mcel-lab.js")
    assert "Run Autopilot Proof" in app
    assert "Autopilot Proof" in app
    assert "mcel-lab-autopilot" in app
    assert "mcel-supervisor-report" in app
    assert "lastSupervisorReport" in bindings
    assert "mcelRunAutopilot" in bindings
    assert "mcelSupervisorReport" in bindings
    assert "var McelLabSupervisor" in supervisor
    assert "runFullProof" in supervisor
    assert "buildQualityGate" in supervisor
    assert "mcel-supervisor-autopilot-proof" in supervisor
    assert "Serializer Firewall" in supervisor
    assert "evidence-packet" in supervisor
    assert 'autopilot: ["autopilot", "full-proof", "prove-all", "quality-gate", "readiness"]' in command_surface
    assert "mcelLabDependenciesReady" in ui and "window.McelLabSupervisor" in ui and "window.McelLabKernel" in ui
    assert "scheduleMcelAutopilotProof" in ui
    assert "runMcelAutopilotProof" in ui
    assert "renderMcelSupervisorReport" in ui
    assert "MCEL_AUTOPILOT_READY" in ui


def test_mcel_lab_eighth_slice_adds_kernel_traceability_and_zero_debt_ledger() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    command_surface = (WEB_APP / "scripts" / "mcel-command-surface.js").read_text(encoding="utf-8")
    ops_runner = (WEB_APP / "scripts" / "mcel-ops-runner.js").read_text(encoding="utf-8")
    acid = (WEB_APP / "scripts" / "mcel-acid-tests.js").read_text(encoding="utf-8")
    harness = (WEB_APP / "scripts" / "mcel-test-harness.js").read_text(encoding="utf-8")
    supervisor = (WEB_APP / "scripts" / "mcel-supervisor.js").read_text(encoding="utf-8")
    kernel = (WEB_APP / "scripts" / "mcel-kernel.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    style = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-kernel.js -->" in html
    assert html.index("mcel-supervisor.js") < html.index("mcel-kernel.js") < html.index("mcel-core.js") < html.index("mcel-lab.js")
    assert "Run Kernel Audit" in app
    assert "Build Traceability Map" in app
    assert "Kernel Audit" in app
    assert "Traceability Map" in app
    assert "Prior Art" in app
    assert "mcelRunKernel" in bindings
    assert "mcelBuildTraceability" in bindings
    assert "mcelKernelReport" in bindings
    assert "mcelTraceabilityReport" in bindings
    assert "mcelPriorArtReport" in bindings
    assert "var McelLabKernel" in kernel
    assert "moduleManifest" in kernel
    assert "priorArtMatrix" in kernel
    assert "requirementLedger" in kernel
    assert "runKernelAudit" in kernel
    assert "compactTraceabilityText" in kernel
    assert "MCEL PRIOR ART / DIFFICULTY RESOLUTION" in kernel
    assert "React" in kernel and "Web Components" in kernel and "GrapesJS" in kernel
    assert 'kernel: ["kernel", "boot-audit", "module-audit"]' in command_surface
    assert '"prior-art": ["prior-art", "precedent", "references", "reference-map"]' in command_surface
    assert "kernelClean" in ops_runner
    assert "Kernel Audit" in supervisor
    assert "kernelReport" in supervisor
    assert "kernel audit maps modules, requirements, prior art, and debt gates" in harness
    assert "runMcelKernelAudit" in ui
    assert "buildMcelTraceabilityMap" in ui
    assert "renderMcelPriorArtReport" in ui
    assert "#mcel-kernel-report" in style


def test_mcel_lab_diagnostics_are_collapsed_to_keep_primary_surface_focused() -> None:
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    style = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    diagnostics_start = app.index('<details id="mcel-diagnostics-drawer"')
    workbench_start = app.index('<div class="mcel-lab-workbench">')
    assert workbench_start < diagnostics_start
    assert '<details id="mcel-diagnostics-drawer" class="mcel-lab-diagnostics"' in app
    assert '<details id="mcel-diagnostics-drawer" class="mcel-lab-diagnostics" open' not in app
    assert "Diagnostics &amp; Proofs" in app
    assert app.index("Run Full Contract Suite") > diagnostics_start
    assert app.index("Run Selected Acid Test") > diagnostics_start
    assert app.index("Scenario Matrix") > diagnostics_start
    assert app.index("Kernel Audit") > diagnostics_start
    assert "Primary surface stays product-first: the editor and the rendered site open in isolated modal surfaces instead of fighting the lab chrome." in app
    assert "Open Site Editor" in app
    assert "Open Rendered Site" in app
    assert "mcel-editor-modal" in app
    assert "mcel-site-modal" in app
    assert "mcel-site-frame" in app
    assert "mcel-site-frame-status" in app
    assert "mcel-site-frame-log" in app
    assert "mcel-site-frame-resync" in app
    assert "mcel-site-frame-rebuild" in app
    assert "mcel-site-frame-clear" in app
    assert "mcel-site-frame-mini-status" in app
    assert "mcel-runtime-measurement-well" in app
    assert "mcelOpenEditorModal" in bindings
    assert "mcelSiteFrameStatus" in bindings
    assert "siteFrameTwiddle" in bindings
    assert "openMcelLabModal" in ui
    assert "syncMcelRenderedSiteFrame" in ui
    assert "isolatedSiteDocument" in ui
    assert "recordMcelSiteFrameTwiddle" in ui
    assert "rebuildMcelSiteFrameShell" in ui
    assert "clearMcelSiteFrameSrcdoc" in ui
    assert "MCEL_SITE_IFRAME_LOADED" in ui
    assert 'if (event.target === modal) closeMcelLabModal("all")' in ui
    assert 'mcel-lab-modal[aria-hidden="true"]' in style
    assert "mcel-site-frame" in style
    assert "mcel-site-frame-twiddle" in style
    assert "mcelDiagnosticsDrawer" in bindings
    assert "openMcelDiagnosticsDrawer" in ui
    assert "MCEL_DIAGNOSTICS_OPENED" in ui
    assert ".mcel-lab-diagnostics" in style
    assert ".mcel-lab-diagnostics-actions" in style


def test_mcel_lab_acid_tests_stress_runtime_editor_serializer_and_evidence_contracts() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    command_surface = (WEB_APP / "scripts" / "mcel-command-surface.js").read_text(encoding="utf-8")
    ops_runner = (WEB_APP / "scripts" / "mcel-ops-runner.js").read_text(encoding="utf-8")
    acid = (WEB_APP / "scripts" / "mcel-acid-tests.js").read_text(encoding="utf-8")
    harness = (WEB_APP / "scripts" / "mcel-test-harness.js").read_text(encoding="utf-8")
    supervisor = (WEB_APP / "scripts" / "mcel-supervisor.js").read_text(encoding="utf-8")
    kernel = (WEB_APP / "scripts" / "mcel-kernel.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    style = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-acid-tests.js -->" in html
    assert html.index("mcel-ops-runner.js") < html.index("mcel-acid-tests.js") < html.index("mcel-test-harness.js")
    assert "Run Selected Acid Test" in app
    assert "Run Full Acid Suite" in app
    assert "Acid Tests" in app
    assert "mcel-lab-acid" in app
    assert "mcel-lab-acid-suite" in app
    assert "mcel-acid-select" in app
    assert "mcel-acid-report" in app
    assert "mcelRunAcid" in bindings
    assert "mcelRunAcidSuite" in bindings
    assert "mcelAcidSelect" in bindings
    assert "mcelAcidReport" in bindings
    assert "lastAcidReport" in bindings
    assert "var McelLabAcidTests" in acid
    assert "listCases" in acid
    assert "runOne" in acid
    assert "runtime pollution firewall strips hostile generated/source-owned junk" in acid
    assert "catastrophic repair restores canonical generated parts" in acid
    assert "semantic command fuzz keeps clean source contract" in acid
    assert "scenario × theme soak preserves serializer/a11y/CSS law" in acid
    assert "operational evidence packet remains machine-checkable" in acid
    assert "overflow law forbids internal scrollbar" in acid
    assert "delegated scroll policy publishes runtime owner" in acid
    assert "hasRuntimeLeak" in acid
    assert "acid: [\"acid\", \"acid-test\", \"acid-tests\", \"stress-proof\", \"torture\", \"fuzz\", \"hostile\"]" in command_surface
    assert "acidClean" in ops_runner
    assert "acid tests:" in ops_runner
    assert "acid tests survive hostile runtime/editor/serializer pressure" in harness
    assert "Acid Tests" in supervisor
    assert "acidReport" in supervisor
    assert "acid-tests" in kernel
    assert "fuzz testing" in kernel
    assert "Hostile stress tests prove serializer/editor/runtime resilience" in kernel
    assert "runSelectedMcelAcidTest" in ui
    assert "runMcelAcidTests" in ui
    assert "renderMcelAcidTests" in ui
    assert "MCEL_SELECTED_ACID_TEST_PASSED" in ui
    assert "MCEL_ACID_SUITE_PASSED" in ui
    assert "#mcel-acid-report" in style


def test_mcel_lab_heavy_proofs_are_manual_only_and_scenario_load_is_lightweight() -> None:
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    supervisor = (WEB_APP / "scripts" / "mcel-supervisor.js").read_text(encoding="utf-8")
    acid = (WEB_APP / "scripts" / "mcel-acid-tests.js").read_text(encoding="utf-8")
    style = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert "renderMcelAutopilotDeferred(\"boot\")" in ui
    assert 'scheduleMcelAutopilotProof("boot-autopilot")' not in ui
    assert "renderMcelAutopilotDeferred(`scenario:${scenario.id}`)" in ui
    assert "scheduleMcelAutopilotProof(`scenario:${scenario.id}`)" not in ui
    assert "Scenario changes and page load intentionally do not run matrix, acid, kernel, or autopilot suites." in ui
    assert "runHeavyProofs: false" in ui
    assert "const runHeavyProofs = Boolean(options.runHeavyProofs)" in supervisor
    assert "runHeavyProofs ? opsRunner.runScenarioMatrix()" in supervisor
    assert "runHeavyProofs ? acidTests()?.runAll" in supervisor
    assert "Run Selected Acid Test" in app
    assert "Run Full Acid Suite" in app
    assert "mcel-acid-select" in app
    assert "mcelRunAcidSuite" in bindings
    assert "mcelAcidSelect" in bindings
    assert "populateMcelAcidCases" in ui
    assert "runSelectedMcelAcidTest" in ui
    assert "MCEL_SELECTED_ACID_TEST_PASSED" in ui
    assert "MCEL_ACID_SUITE_PASSED" in ui
    assert "executionMode: ${report.executionMode" in acid
    assert "mcel-acid-picker" in style


def test_mcel_lab_ninth_slice_adds_layout_law_browser_observer_and_public_core_api() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    contract = (WEB_APP / "scripts" / "mcel-contract.js").read_text(encoding="utf-8")
    engine = (WEB_APP / "scripts" / "mcel-engine.js").read_text(encoding="utf-8")
    editor = (WEB_APP / "scripts" / "mcel-editor.js").read_text(encoding="utf-8")
    law_registry = (WEB_APP / "scripts" / "mcel-law-registry.js").read_text(encoding="utf-8")
    browser_observer = (WEB_APP / "scripts" / "mcel-browser-observer.js").read_text(encoding="utf-8")
    layout_law = (WEB_APP / "scripts" / "mcel-layout-law.js").read_text(encoding="utf-8")
    command_surface = (WEB_APP / "scripts" / "mcel-command-surface.js").read_text(encoding="utf-8")
    scenarios = (WEB_APP / "scripts" / "mcel-scenarios.js").read_text(encoding="utf-8")
    graph = (WEB_APP / "scripts" / "mcel-graph.js").read_text(encoding="utf-8")
    ops_runner = (WEB_APP / "scripts" / "mcel-ops-runner.js").read_text(encoding="utf-8")
    acid = (WEB_APP / "scripts" / "mcel-acid-tests.js").read_text(encoding="utf-8")
    harness = (WEB_APP / "scripts" / "mcel-test-harness.js").read_text(encoding="utf-8")
    supervisor = (WEB_APP / "scripts" / "mcel-supervisor.js").read_text(encoding="utf-8")
    kernel = (WEB_APP / "scripts" / "mcel-kernel.js").read_text(encoding="utf-8")
    core = (WEB_APP / "scripts" / "mcel-core.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    style = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert html.index("mcel-law-registry.js") < html.index("mcel-style-law.js")
    assert html.index("mcel-browser-observer.js") < html.index("mcel-layout-law.js") < html.index("mcel-command-surface.js")
    assert html.index("mcel-kernel.js") < html.index("mcel-core.js") < html.index("mcel-lab.js")
    assert "Layout / Geometry Law" in app
    assert "mcel-trait-size-policy" in app
    assert "mcel-trait-overflow-policy" in app
    assert "mcel-trait-scroll-policy" in app
    assert "mcelLayoutLawReport" in bindings
    assert 'sizePolicy: "data-mc-size-policy"' in contract
    assert 'overflowPolicy: "data-mc-overflow-policy"' in contract
    assert 'scrollPolicy: "data-mc-scroll-policy"' in contract
    assert "layoutPolicies" in contract
    assert "data-mc-overflow-computed" in contract
    assert "layout source policies survive while observed geometry is stripped" in engine
    assert "scrollRegions" in engine
    assert "allowedSizePolicies" in editor
    assert "McelLabLawRegistry" in law_registry and "buildAxisMatrix" in law_registry
    assert "McelLabBrowserObserver" in browser_observer and "observeElement" in browser_observer
    assert "McelLabLayoutLaw" in layout_law and "layout.overflow.scroll.v2" in layout_law
    assert "repairRuntimeLaw" in layout_law and "proveRuntime" in layout_law
    assert 'layout: ["layout", "geometry", "overflow-proof", "scroll-proof"]' in command_surface
    assert "never scroll" in acid
    assert "Layout Overflow Proof" in scenarios
    assert "scrollOwner" in graph and "layoutProofed" in graph
    assert "layout law clean" in ops_runner
    assert "Layout / Geometry Law" in supervisor
    assert "browser-observation" in kernel
    assert "public-core-api" in kernel
    assert "window.MCEL = MCEL" in core
    assert "compile," in core and "serialize," in core and "audit," in core
    assert "renderMcelLayoutLawReport" in ui
    assert "window.MCEL" in ui
    assert 'data-mc-scroll-owner="self"' in style



def test_mcel_lab_tenth_slice_fleshes_out_platform_subsumption_spine() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    contract = (WEB_APP / "scripts" / "mcel-contract.js").read_text(encoding="utf-8")
    engine = (WEB_APP / "scripts" / "mcel-engine.js").read_text(encoding="utf-8")
    editor = (WEB_APP / "scripts" / "mcel-editor.js").read_text(encoding="utf-8")
    command_surface = (WEB_APP / "scripts" / "mcel-command-surface.js").read_text(encoding="utf-8")
    ops_runner = (WEB_APP / "scripts" / "mcel-ops-runner.js").read_text(encoding="utf-8")
    supervisor = (WEB_APP / "scripts" / "mcel-supervisor.js").read_text(encoding="utf-8")
    kernel = (WEB_APP / "scripts" / "mcel-kernel.js").read_text(encoding="utf-8")
    core = (WEB_APP / "scripts" / "mcel-core.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")

    new_modules = [
        "mcel-component-law.js",
        "mcel-state-law.js",
        "mcel-data-law.js",
        "mcel-form-law.js",
        "mcel-action-law.js",
        "mcel-render-law.js",
        "mcel-a11y-law.js",
        "mcel-performance-law.js",
        "mcel-platform-spine.js",
        "mcel-workbench.js",
        "mcel-browser-runner.js",
        "mcel-site-skeleton.js",
    ]
    for name in new_modules:
        module_text = (WEB_APP / "scripts" / name).read_text(encoding="utf-8")
        assert "<!-- @include applications/scripts/" + name + " -->" in html
        assert "var McelLab" in module_text
        assert "buildSubsumptionPlan" in module_text or "buildSubsumptionLattice" in module_text or "observeAndProve" in module_text or "buildWorkbenchPlan" in module_text or "buildSkeleton" in module_text

    assert html.index("mcel-layout-law.js") < html.index("mcel-component-law.js") < html.index("mcel-platform-spine.js") < html.index("mcel-workbench.js") < html.index("mcel-browser-runner.js") < html.index("mcel-command-surface.js")
    assert 'contractVersion = "mcel-lab.v0.11-ui-site-skeleton"' in contract
    assert 'componentName: "data-mc-component"' in contract
    assert 'stateOwner: "data-mc-state-owner"' in contract
    assert 'query: "data-mc-query"' in contract
    assert 'submit: "data-mc-submit"' in contract
    assert 'renderMode: "data-mc-render"' in contract
    assert 'a11yPolicy: "data-mc-a11y-policy"' in contract
    assert 'performanceBudget: "data-mc-performance-budget"' in contract
    assert "platformPolicies" in contract
    assert "data-mc-component-law" in contract
    assert "platform source policies survive while runtime proof facts are stripped" in engine
    assert "componentName" in editor and "renderMode" in editor and "performanceBudget" in editor
    assert "subsumption: [\"subsumption\", \"obsolete\", \"replace-frameworks\", \"rust-java\", \"platform-spine\"]" in command_surface
    assert "tanstack" in command_surface and "react" in command_surface
    assert "platformSpine" in ops_runner and "subsumptionLattice" in ops_runner
    assert "Platform Spine" in supervisor and "Browser Semantic Proof" in supervisor
    assert "component-subsumption" in kernel
    assert "state-subsumption" in kernel
    assert "data-subsumption" in kernel
    assert "form-subsumption" in kernel
    assert "render-subsumption" in kernel
    assert "platform-spine" in kernel
    assert "workbench-subsumption" in kernel
    assert "browser-runner" in kernel
    assert "buildSubsumptionLattice" in core and "buildWorkbenchPlan" in core and "runBrowserProof" in core
    assert "Semantic Component" in app and "Lawful Form" in app and "Semantic Route" in app
    assert "Build Subsumption Lattice" in app and "Build Workbench Plan" in app and "Run Browser Semantic Proof" in app
    assert "mcelBuildSubsumption" in bindings and "mcelSubsumptionReport" in bindings
    assert "buildMcelSubsumptionLattice" in ui
    assert "renderMcelSubsumptionLattice" in ui
    assert "runMcelBrowserSemanticProof" in ui


def test_mcel_lab_eleventh_slice_makes_real_ui_skeleton_visible_and_scroll_safe() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    contract = (WEB_APP / "scripts" / "mcel-contract.js").read_text(encoding="utf-8")
    layout_law = (WEB_APP / "scripts" / "mcel-layout-law.js").read_text(encoding="utf-8")
    site_skeleton = (WEB_APP / "scripts" / "mcel-site-skeleton.js").read_text(encoding="utf-8")
    scenarios = (WEB_APP / "scripts" / "mcel-scenarios.js").read_text(encoding="utf-8")
    acid = (WEB_APP / "scripts" / "mcel-acid-tests.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    style = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-site-skeleton.js -->" in html
    assert html.index("mcel-browser-runner.js") < html.index("mcel-site-skeleton.js") < html.index("mcel-command-surface.js")
    assert "Semantic Interface Forge" in app
    assert "Minimal site acid test" in app
    assert "mcel-ui-skeleton-summary" in app
    assert "mcel-ui-skeleton-health" in app
    assert "NeighborhoodMarketSite" in contract
    assert "HeroSection" in contract
    assert "TrustCluster" in contract
    assert "SignupForm" in contract
    assert "FooterCta" in contract
    assert "mcel-lab.v0.11-ui-site-skeleton" in contract
    assert "Auto scroll is content-expanding by default" in layout_law
    assert 'return "content";' in layout_law
    assert 'data-mc-scroll-owner="content"' in style
    assert "McelLabSiteSkeleton" in site_skeleton
    assert "buildSkeleton" in site_skeleton
    assert "nestedScrollbarCount" in site_skeleton
    assert "minimal-site-skeleton" in scenarios
    assert "without accidental nested scrollbars" in scenarios
    assert "minimal-site-skeleton-no-scroll-traps" in acid
    assert "acidMinimalSiteSkeletonNoScrollTraps" in acid
    assert "mcelUiSkeletonSummary" in bindings and "mcelUiSkeletonHealth" in bindings
    assert "renderMcelSiteSkeleton" in ui
    assert "lastSiteSkeleton" in bindings
    assert "iframe" in app and "sandbox" in app
    assert "isolated from lab chrome" in app


def test_mcel_lab_chrome_fit_remediation_protocol_is_chrome_owned_and_runtime_only() -> None:
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    chrome_law = (WEB_APP / "scripts" / "mcel-chrome-law.js").read_text(encoding="utf-8")
    browser_observer = (WEB_APP / "scripts" / "mcel-browser-observer.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")

    assert 'CHROME_ID_ATTR = "data-mcel-chrome-id"' in chrome_law
    assert 'FIT_REGION_ATTR = "data-mcel-fit-region"' in chrome_law
    assert 'FIT_POLICY_ATTR = "data-mcel-fit-policy"' in chrome_law
    assert 'FIT_REMEDIATION_ATTR = "data-mcel-fit-remediation"' in chrome_law
    assert "chromeFitContract" in chrome_law
    assert "chromeCompositionContract" in chrome_law
    assert "chromeRemediationPlan" in chrome_law
    assert "compositionContract" in chrome_law
    assert '"primary-control-width-collapsed-relative-to-input"' in chrome_law
    assert '"content-fit-failed"' in chrome_law
    assert '"shape-interior-escape"' in chrome_law
    assert '"shape-containment-failed"' in chrome_law
    assert '"text-distorted-by-narrow-inline-size"' in chrome_law
    assert '"container-distorted-by-extreme-aspect-ratio"' in chrome_law
    assert '"control-balance"' in chrome_law
    assert '"smart-flow-frame"' in chrome_law
    assert '"shape-inset-content"' in chrome_law
    assert '"smart-content-envelope"' in chrome_law
    assert '"dedistort-inline-content"' in chrome_law
    assert '"dedistort-container-shape"' in chrome_law
    assert '"content-negotiate"' in chrome_law
    assert '"object-grow"' in chrome_law
    assert '"object-reshape"' in chrome_law
    assert '"region-reflow"' in chrome_law
    assert 'element.setAttribute(FIT_REGION_ATTR, fit.region)' in chrome_law
    assert 'element.setAttribute(FIT_POLICY_ATTR, fit.policy)' in chrome_law
    assert 'element.setAttribute(CHROME_FRAME_ATTR, frame)' in chrome_law
    assert 'element.setAttribute(CHROME_REGION_ROLE_ATTR, role)' in chrome_law
    assert 'CHROME_PRIMITIVE_ATTR = "data-mcel-chrome-primitive"' in chrome_law
    assert 'frame.setAttribute(CHROME_PRIMITIVE_ATTR, primitive)' in chrome_law
    assert 'generatedFramePrimitive' in chrome_law
    assert '".mcel-chrome-compact-panel > [data-mcel-chrome-region-role=\\"body\\"]"' in chrome_law
    assert '".mcel-chrome-journey-step > [data-mcel-chrome-frame]"' in chrome_law

    assert "observeChromeFit" in browser_observer
    assert "observeChromeComposition" in browser_observer
    assert "chromeSupportsCompositionObservation" in browser_observer
    assert 'if (chrome !== "chrome-editorial-flow") return []' not in browser_observer
    assert "shapeInteriorEscapeFor" in browser_observer
    assert "shapeContainmentChildrenFor" in browser_observer
    assert "contentFitFailureFor" in browser_observer
    assert "content-fit-failed" in browser_observer
    assert "shape-containment-failed" in browser_observer
    assert "safeShapeIntervalAtY" in browser_observer
    assert "textDistortionFor" in browser_observer
    assert "containerDistortionFor" in browser_observer
    assert "text-distorted-by-narrow-inline-size" in browser_observer
    assert "container-distorted-by-extreme-aspect-ratio" in browser_observer
    assert "compositionWarnings" in browser_observer
    assert '"mcel-chrome-fit-report"' in browser_observer
    assert '"page-overflow"' in browser_observer
    assert '"inline-overflow"' in browser_observer
    assert '"child-escape"' in browser_observer
    assert '"hard-object-overflow"' in browser_observer
    assert "data-mcel-chrome-generated" in browser_observer

    assert 'sandbox="allow-same-origin"' in app
    assert "runMcelSiteFrameChromeFit" in ui
    assert "data-mcel-fit-remediation" in ui
    assert "data-mcel-composition-remedy" in ui
    assert "data-mcel-composition-warnings" in ui
    assert "mcelChromeCompositionScopeSelector" in ui
    assert "[data-mcel-chrome-frame]" in ui
    assert "[data-mcel-chrome-region-role]" in ui
    assert ".mcel-chrome-compact-panel > .mc {" not in ui
    assert ".mcel-chrome-journey-step > .mc {" not in ui
    assert ".mcel-chrome-cluster-grid" in ui
    assert ".mcel-chrome-spotlight-support" in ui
    assert ".mcel-chrome-journey-step" in ui
    assert ".mcel-chrome-compact-panel" in ui
    assert "control-balance" in ui
    assert "smart-flow-frame" in ui
    assert "shape-inset-content" in ui
    assert "smart-content-envelope" in ui
    assert "dedistort-inline-content" in ui
    assert "dedistort-container-shape" in ui
    assert "text-distorted-by-narrow-inline-size" in ui
    assert "container-distorted-by-extreme-aspect-ratio" in ui
    assert "wantsGeneratedContainer" in ui
    assert "generatedContainerWithSource" in ui
    assert "writing-mode: horizontal-tb" in ui
    assert "border-radius: min(var(--site-radius), 28px) !important" in ui
    assert 'data-mcel-chrome-primitive="content-envelope"' in ui
    assert "contentFlowPrimitiveSelector" in chrome_law
    assert "content-flow" in chrome_law
    assert "--mcel-smart-envelope-block-pad" in ui
    assert "border-radius: 999px;" in ui
    assert '[data-mcel-composition-remedy~="smart-flow-frame"]' in ui
    assert "runCompositionRemediationPasses" in ui
    assert 'body[data-mcel-fit-remediation~="content-negotiate"][data-mcel-chrome="chrome-cluster-grid"]' in ui
    assert 'body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-spotlight"] .mcel-chrome-spotlight-shell' in ui
    assert 'body[data-mcel-fit-remediation~="region-reflow"][data-mcel-chrome="chrome-compact-disclosure"] .mcel-chrome-compact-panel' in ui
    assert "firstPassCompositionWarnings" in ui
    assert "finalCompositionWarnings" in ui
    assert "mcelChromeFitFailureCount" in ui
    assert "summarizeMcelChromeFitReport" in ui
    assert 'fit=${status}' in ui
    assert "lastChromeFitReport" in bindings
    assert "lastChromeFitReport" in ui
    assert 'body class="mcel-site-theme ${theme}" data-mcel-chrome="${chrome}"' in ui


def test_mcel_lab_adds_smart_css_primitive_replacement_lab() -> None:
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    css = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")

    assert 'id="mcel-open-smart-css-modal"' in app
    assert 'id="mcel-smart-css-modal"' in app
    assert "Raw CSS backend hazards on the left; MCEL golden-path primitives on the right." in app
    assert "Rerun Primitive Proofs" in app
    assert "mcelSmartCssModal" in bindings
    assert "mcelSmartCssSuite" in bindings
    assert "lastSmartCssPrimitiveReport" in bindings
    assert "renderMcelSmartCssPrimitiveLab" in ui
    assert "runMcelSmartCssPrimitiveProofs" in ui
    assert "shape-containment-failed" in ui
    assert "content-fit-failed" in ui
    assert "paint-layer-overlay-failed" in ui
    assert "MCEL-generated golden-path surfaces must use smart primitives" in ui
    assert "big-rounded support-frame object with explicit content region and growth contract" in ui
    assert "same decorative paint token, but raw stacking places it above semantic content" in ui
    assert "same decorative paint token, but paint envelope is behind semantic content and inert to hit testing" in ui
    assert "foreground overlap(s)" in ui
    assert "paint z=" in ui
    assert "pointer-events=" in ui
    assert "expected backend hazard detected" in ui
    assert "golden-path smart primitive proof passed" in ui
    assert ".mcel-smart-css-raw-pill .mcel-smart-css-object" in css
    assert ".mcel-smart-css-raw-pill .mcel-smart-css-card" in css
    assert "inline-size: calc(100% + 92px);" in css
    assert ".mcel-smart-css-smart-frame .mcel-smart-css-object" in css
    assert "border-radius: 999px;" in css
    assert "display: none;" in css
    assert ".mcel-smart-css-raw-clip .mcel-smart-css-object" in css
    assert ".mcel-smart-css-smart-flow .mcel-smart-css-object" in css
    assert ".mcel-smart-css-raw-overlay .mcel-smart-css-paint-layer" in css
    assert ".mcel-smart-css-smart-paint .mcel-smart-css-paint-layer" in css
    assert "z-index: 30;" in css
    assert "pointer-events: auto;" in css
    assert "pointer-events: none;" in css


def test_mcel_lab_supercut_v02_architecture_modules_are_wired() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    contracts = (WEB_APP / "scripts" / "mcel-supercut-contracts.js").read_text(encoding="utf-8")
    blackboard = (WEB_APP / "scripts" / "mcel-supercut-blackboard.js").read_text(encoding="utf-8")
    registry = (WEB_APP / "scripts" / "mcel-supercut-registry.js").read_text(encoding="utf-8")
    core_pack = (WEB_APP / "scripts" / "mcel-supercut-packs-core.js").read_text(encoding="utf-8")
    git_pack = (WEB_APP / "scripts" / "mcel-supercut-packs-git-tools.js").read_text(encoding="utf-8")
    task_pack = (WEB_APP / "scripts" / "mcel-supercut-packs-task-manager.js").read_text(encoding="utf-8")
    planner_domains_pack = (WEB_APP / "scripts" / "mcel-supercut-packs-planner-domains.js").read_text(encoding="utf-8")
    core = (WEB_APP / "scripts" / "mcel-supercut-core.js").read_text(encoding="utf-8")
    supercut = (WEB_APP / "scripts" / "mcel-supercut.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-supercut-contracts.js -->" in html
    assert "<!-- @include applications/scripts/mcel-supercut-blackboard.js -->" in html
    assert "<!-- @include applications/scripts/mcel-supercut-registry.js -->" in html
    assert "<!-- @include applications/scripts/mcel-supercut-packs-core.js -->" in html
    assert "<!-- @include applications/scripts/mcel-supercut-packs-git-tools.js -->" in html
    assert "<!-- @include applications/scripts/mcel-supercut-packs-task-manager.js -->" in html
    assert "<!-- @include applications/scripts/mcel-supercut-packs-planner-domains.js -->" in html
    assert "<!-- @include applications/scripts/mcel-supercut-core.js -->" in html
    assert (
        html.index("task-manager-mcel.js")
        < html.index("mcel-supercut-contracts.js")
        < html.index("mcel-supercut-blackboard.js")
        < html.index("mcel-supercut-registry.js")
        < html.index("mcel-supercut-packs-core.js")
        < html.index("mcel-supercut-packs-git-tools.js")
        < html.index("mcel-supercut-packs-task-manager.js")
        < html.index("mcel-supercut-packs-planner-domains.js")
        < html.index("mcel-supercut-core.js")
        < html.index("mcel-supercut.js")
        < html.index("git-tools-mcel.js")
    )

    assert "global.McelSupercutContracts" in contracts
    assert "component.operational-action" in contracts
    assert "component.remote-mutation-action" in contracts
    assert "no-command-execution" in contracts

    assert "global.McelSupercutBlackboard" in blackboard
    assert "records: []" not in blackboard
    assert "rewritePreview" in blackboard
    assert "unsafeActionsBlocked" in blackboard

    assert "contractPriority" in blackboard
    assert "strongestRisk" in blackboard
    assert "riskPolicy?.blocked" in blackboard
    assert "candidatePriority" in blackboard
    assert "prioritizeCandidateElements" in blackboard
    assert "data-mc-component-kind" in blackboard
    assert "directElementText" in blackboard
    assert "directText: record.directText" in blackboard
    assert "countUnsafeBlocked" in core
    assert "normalizeComponentsBeforeRewrite" in core
    assert "taskManagerUnsafeFamilyKey" in core
    assert "unsafeActionInstancesBlocked" in core
    assert 'countUnsafeBlocked(blackboard, "families")' in core
    assert 'countUnsafeBlocked(blackboard, "instances")' in core
    assert 'component.contract = "component.root"' in core
    assert 'component.rewriteTag = "mcel-app"' in core
    assert "isBlockedSurfaceContract" in core
    assert "shouldEnforceBlockingPolicy" in core
    assert "executableSurfaceRecord" in blackboard
    assert "executableSurfaceRecord" in core
    assert "addIfExecutable" in core
    assert "commandSnippet" in core_pack
    assert "component.workflow" in core
    assert "contract?.proofPolicy" in core
    assert "identitySource" in core_pack
    assert "explicitPanelSignal" in core_pack
    assert '["header", "footer"].includes(tag)) return record.depth <= 2' in core_pack
    assert "isMajorSection = tag === \"section\"" in core_pack
    assert "nonRootDomainSurface" in git_pack
    assert "domainSource" in git_pack
    assert "record?.directText" in git_pack
    assert "global.McelSupercutRegistry" in registry
    assert "PHASE_ORDER" in registry
    assert "duplicate rule id rejected" in registry
    assert "priority" in registry

    assert 'id: "core-html"' in core_pack
    assert 'id: "core-action-risk"' in core_pack
    assert "core.detect.regions-disciplined" in core_pack
    assert "core.audit.region-discipline" in core_pack

    assert 'id: "git-tools-domain"' in git_pack
    assert "git-tools.risk.start-server" in git_pack
    assert "git-tools.risk.kill-pid" in git_pack
    assert "git-tools.risk.manual-command" in git_pack
    assert "no-submit" in git_pack
    assert "global.McelSupercutPacksPlannerDomains" in planner_domains_pack
    assert "PLANNER_DOMAIN_PLANS" in planner_domains_pack
    assert 'id: "terminal-domain"' in planner_domains_pack
    assert 'id: "wallet-domain"' in planner_domains_pack

    assert "git-tools.risk.known-dangerous-controls" in git_pack
    assert "KNOWN_DANGEROUS_CONTROLS" in git_pack
    assert "executableControl(record)" in git_pack
    assert "git-server-remote-add" in git_pack
    assert "git-server-remote-run" in git_pack

    assert 'id: "task-manager-domain"' in task_pack
    assert "task-manager.detect-root" in task_pack
    assert "task-manager.risk.known-controls" in task_pack
    assert "task-manager.risk.pid-control" in task_pack
    assert "task-manager.risk.schedule-mutation" in task_pack
    assert "KNOWN_RISKY_CONTROLS" in task_pack
    assert "task-server-shutdown" in task_pack
    assert "data-task-action" in task_pack

    assert "git-tools.detect-root" in git_pack
    assert "isAppRootRecord" in git_pack
    assert "nonRootDomainSurface(record, blackboard)" in git_pack

    assert "global.McelSupercutCore" in core
    assert "summarizeRewritePreview" in core
    assert "sourceRewrite: \"disabled\"" in core
    assert "remoteMirrorPushManualCommand: \"blocked\"" in core
    assert "task-manager-domain" in core

    assert "shouldEnforceBlockingPolicy" in core
    assert "executableSurfaceRecord" in blackboard
    assert "executableSurfaceRecord" in core
    assert "addIfExecutable" in core
    assert "commandSnippet" in core_pack
    assert "component.workflow" in core

    assert "global.McelSupercutCore.run" in supercut
    assert "rewritePreviewCount" in supercut
    assert "blackboardRecordCount" in supercut
    assert "runtimeSourceMutations" in supercut
    assert "TASK_MANAGER_PURPOSE_RULES" in supercut
    assert "purposeRulesForApp" in supercut
    assert "task-manager.server-control" in supercut
    assert "task-manager.process-feed" in supercut


def test_mcel_lab_supercut_v02_is_visible_in_git_tools_lens() -> None:
    lab = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    adapter = (WEB_APP / "scripts" / "git-tools-mcel.js").read_text(encoding="utf-8")

    assert 'packs: options.packs || ["core-html", "core-action-risk", "git-tools-domain"]' in adapter
    assert "summarizeSupercutRewritePreview" in adapter
    assert "supercutPacksLoaded" in adapter
    assert "supercutRulesFired" in adapter
    assert "supercutBlackboardRecordCount" in adapter
    assert "supercutRewritePreviewCount" in adapter
    assert "supercutUnsafeActionsBlocked" in adapter
    assert "supercutRuntimeSourceMutations" in adapter

    assert "Supercut Architecture" in lab
    assert "Rewrite preview:" in lab
    assert "packs loaded:" in lab
    assert "rules fired:" in lab
    assert "blackboard records:" in lab
    assert "rewrite-preview nodes:" in lab
    assert "explanations ready:" in lab
    assert "unsafe actions blocked:" in lab
    assert "status feeds:" in lab
    assert "rewrite preview ${enrichment.supercutRewritePreviewCount" in lab


def test_mcel_lab_supercut_v02_is_visible_in_task_manager_lens() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    adapter = (WEB_APP / "scripts" / "task-manager-mcel.js").read_text(encoding="utf-8")
    task_pack = (WEB_APP / "scripts" / "mcel-supercut-packs-task-manager.js").read_text(encoding="utf-8")
    registry = (WEB_APP / "scripts" / "mcel-supercut-registry.js").read_text(encoding="utf-8")
    supercut = (WEB_APP / "scripts" / "mcel-supercut.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-supercut-packs-task-manager.js -->" in html
    assert html.index("mcel-supercut-packs-git-tools.js") < html.index("mcel-supercut-packs-task-manager.js") < html.index("mcel-supercut-core.js")
    assert "runs MCEL Supercut domain packs" in app
    assert "Task Manager schedule actions are observed" in app

    assert 'packs: options.packs || ["core-html", "core-action-risk", "task-manager-domain"]' in adapter
    assert "runTaskManagerSupercutTranslation" in adapter
    assert "supercutArchitectureStatus" in adapter
    assert "supercutRewritePreviewCount" in adapter
    assert "supercutUnsafeActionsBlocked" in adapter
    assert "supercutRuntimeSourceMutations" in adapter
    assert "clearTaskManagerSupercutTranslation" in adapter

    assert 'id: "task-manager-domain"' in task_pack
    assert "task-manager.server-control" in task_pack
    assert "task-manager.process-control.kill-pid" in task_pack
    assert "task-manager.process-control.terminate-pid" in task_pack
    assert "task-manager.schedule.create" in task_pack
    assert "no-click" in task_pack
    assert "taskManagerDomainPack" in registry
    assert "TASK_MANAGER_PURPOSE_RULES" in supercut
    assert "purposeRulesForApp(app, root)" in supercut
    assert "task-manager.schedule-workflow" in supercut
    assert "git-tools.gitea-server-control" in supercut



def test_mcel_lab_adds_purpose_aware_specimen_planner() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    lab = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    planner = (WEB_APP / "scripts" / "mcel-specimen-planner.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-specimen-planner.js -->" in html
    assert html.index("mcel-specimen-planner.js") < html.index("mcel-lab.js")

    assert 'id="mcel-canonical-app-plan"' in app
    assert 'id="mcel-canonical-app-plan-summary"' in app
    assert 'id="mcel-canonical-app-plan-list"' in app
    assert "purpose-aware mount hints" in app

    assert "lastCanonicalSpecimenPlan" in bindings
    assert "mcelCanonicalAppPlanSummary" in bindings
    assert "mcelCanonicalAppPlanList" in bindings

    assert "global.McelSpecimenPlanner" in planner
    assert "PLANNER_VERSION" in planner
    assert "mountQueue" in planner
    assert "plannerSnapshot" in planner
    assert "inspectMountedDocument" in planner
    assert "task-manager-domain" in planner
    assert "git-tools-domain" in planner
    assert "spreadsheet-domain" in planner
    assert "terminal-domain" in planner
    assert "email-domain" in planner
    assert "wallet-domain" in planner
    assert "code-editor-domain" in planner
    assert "neverExecute" in planner
    assert "decodeHints" in planner

    assert "mcelCanonicalAppPlannerPlan" in lab
    assert "renderMcelCanonicalAppPlanner" in lab
    assert "McelSpecimenPlanner" in lab
    assert "knownRiskFamilies" in lab
    assert "Mounted evidence" in lab

def test_mcel_lab_turns_on_planner_specimens_in_dropdown() -> None:
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    lab = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    planner = (WEB_APP / "scripts" / "mcel-specimen-planner.js").read_text(encoding="utf-8")

    expected = {
        "task-manager": "#task-manager-app",
        "git-tools": "#git-tools-app",
        "calculator": "#calculator-app",
        "document": "#document-app",
        "spreadsheet": "#spreadsheet-app",
        "onlyoffice": "#onlyoffice-app",
        "terminal": "#terminal-app",
        "chat-console": "#chat-console-app",
        "email": "#email-app",
        "code-editor": "#code-editor-app",
        "file-explorer": "#file-explorer-app",
        "website-builder": "#website-builder-app",
        "worker": "#worker-app",
        "wallet": "#wallet-app",
        "game-editor": "#game-editor-app",
        "webgl": "#webgl-demo",
        "mcel-lab": "#mcel-lab-app",
    }

    select_block = app[
        app.index('id="mcel-canonical-app-select"'):
        app.index("</select>", app.index('id="mcel-canonical-app-select"'))
    ]
    for specimen, root in expected.items():
        assert f'value="{specimen}"' in select_block
        assert f'data-root="{root}"' in select_block
        assert f'"{specimen}": Object.freeze' in planner

    assert select_block.count("<option") == len(expected)
    assert "data-planner-status=" in select_block
    assert "data-point=" in select_block

    assert "createGenericAdapter" in planner
    assert "applyCanonicalMcelSemantics" in planner
    assert "planner-read-only" in planner
    assert "canonicalOptions" in planner
    assert 'if (specimen?.app === "task-manager") return window.TaskManagerMcel || null;' in lab
    assert "createGenericAdapter?.(plan)" in lab
    assert "requiredIdsFor?.(plan)" in lab
    assert "dangerousSelectorsFor?.(plan)" in lab



def test_mcel_lab_calculator_supercut_domain_pack_is_wired() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    registry = (WEB_APP / "scripts" / "mcel-supercut-registry.js").read_text(encoding="utf-8")
    core = (WEB_APP / "scripts" / "mcel-supercut-core.js").read_text(encoding="utf-8")
    planner = (WEB_APP / "scripts" / "mcel-specimen-planner.js").read_text(encoding="utf-8")
    calculator_pack = (WEB_APP / "scripts" / "mcel-supercut-packs-calculator.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-supercut-packs-calculator.js -->" in html
    assert html.index("mcel-supercut-packs-task-manager.js") < html.index("mcel-supercut-packs-calculator.js") < html.index("mcel-supercut-core.js")

    assert "global.McelSupercutPacksCalculator" in calculator_pack
    assert 'id: "calculator-domain"' in calculator_pack
    assert "calculator.detect-root" in calculator_pack
    assert "calculator.detect-keypad" in calculator_pack
    assert "calculator.detect-display" in calculator_pack
    assert "calculator.rectify-local-actions" in calculator_pack
    assert "calculator-local-action-policy" in calculator_pack
    assert "calculator-domain resolved this as a local non-destructive calculator control" in calculator_pack

    assert "calculatorDomainPack" in registry
    assert '"calculator-domain"' in core
    assert "currentAction && !currentActionBlocked" in core

    assert 'status: "domain-ready"' in planner
    assert 'domainPack: "calculator-domain"' in planner
    assert 'adapter: "planner-generic-adapter"' in planner


def test_mcel_lab_remaining_planner_specimens_have_supercut_domain_packs() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    registry = (WEB_APP / "scripts" / "mcel-supercut-registry.js").read_text(encoding="utf-8")
    core = (WEB_APP / "scripts" / "mcel-supercut-core.js").read_text(encoding="utf-8")
    supercut = (WEB_APP / "scripts" / "mcel-supercut.js").read_text(encoding="utf-8")
    planner = (WEB_APP / "scripts" / "mcel-specimen-planner.js").read_text(encoding="utf-8")
    planner_domains_pack = (WEB_APP / "scripts" / "mcel-supercut-packs-planner-domains.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-supercut-packs-planner-domains.js -->" in html
    assert html.index("mcel-supercut-packs-calculator.js") < html.index("mcel-supercut-packs-planner-domains.js") < html.index("mcel-supercut-core.js")

    expected_domains = [
        "document-domain",
        "spreadsheet-domain",
        "onlyoffice-domain",
        "terminal-domain",
        "chat-console-domain",
        "email-domain",
        "code-editor-domain",
        "file-explorer-domain",
        "website-builder-domain",
        "worker-domain",
        "wallet-domain",
        "game-editor-domain",
        "webgl-domain",
        "mcel-lab-domain",
    ]
    for domain in expected_domains:
        assert f'id: "{domain}"' in planner_domains_pack
        assert f'domainPack: "{domain}"' in planner
        assert domain in core or "plannerDomainPacks" in core

    assert "global.McelSupercutPacksPlannerDomains" in planner_domains_pack
    assert "createPlannerDomainPack" in planner_domains_pack
    assert "detect-risk-actions" in planner_domains_pack
    assert "detect-safe-actions" in planner_domains_pack
    assert "detect-feeds" in planner_domains_pack
    assert "plannerDomainPacks" in registry
    assert "McelSupercutPacksPlannerDomains?.plannerDomainPacks" in registry
    assert 'status: "domain-ready"' in planner
    assert 'status: "high-risk-domain-ready"' in planner
    assert 'adapter: "needs-adapter"' not in planner
    assert "plannerPurposeRulesForApp" in supercut
    assert "GENERIC_PURPOSE_RULES" in supercut
    assert "return plannerPurposeRulesForApp(normalizedApp, root).concat(GENERIC_PURPOSE_RULES)" in supercut
    assert "const PURPOSE_RULES = GIT_TOOLS_PURPOSE_RULES" not in supercut
    assert 'expectedRegions: ["seller-panel", "buyer-policy-panel", "network-hubs", "rental-status"]' in planner_domains_pack
    assert 'family: "worker.network-registration"' in planner_domains_pack
    assert 'family: "worker.payment-rental"' in planner_domains_pack
    assert 'family: "worker.credential-network-mutation"' in planner_domains_pack
    assert 'expectedRegions: ["queue", "job-detail", "worker-status", "runtime-controls", "logs"]' not in planner_domains_pack


def test_mcel_lab_element_library_acid_test_is_wired() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")
    lab = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    css = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")
    registry = (WEB_APP / "scripts" / "mcel-element-registry.js").read_text(encoding="utf-8")
    toolkit = (WEB_APP / "scripts" / "mcel-toolkit-core.js").read_text(encoding="utf-8")
    elements = (WEB_APP / "scripts" / "mcel-elements-core.js").read_text(encoding="utf-8")
    acid = (WEB_APP / "scripts" / "mcel-element-acid-test.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-toolkit-core.js -->" in html
    assert "<!-- @include applications/scripts/mcel-concern-core.js -->" in html
    assert "<!-- @include applications/scripts/mcel-project-concern-workbench.js -->" in html
    assert "<!-- @include applications/scripts/mcel-git-file-basket-treegrid-lab.js -->" in html
    assert "<!-- @include applications/scripts/mcel-element-registry.js -->" in html
    assert "<!-- @include applications/scripts/mcel-elements-core.js -->" in html
    assert "<!-- @include applications/scripts/mcel-element-acid-test.js -->" in html
    assert (
        html.index("mcel-specimen-planner.js")
        < html.index("mcel-toolkit-core.js")
        < html.index("mcel-concern-core.js")
        < html.index("mcel-project-concern-workbench.js")
        < html.index("mcel-git-file-basket-treegrid-lab.js")
        < html.index("mcel-element-registry.js")
        < html.index("mcel-elements-core.js")
        < html.index("mcel-element-acid-test.js")
        < html.index("mcel-lab.js")
    )

    assert "MCEL Element Library Acid Test" in app
    assert 'id="mcel-element-acid-canvas"' in app
    assert 'id="mcel-element-acid-summary"' in app
    assert 'id="mcel-element-acid-report"' in app
    assert 'id="mcel-element-acid-rerun"' in app
    assert "mcelElementAcidCanvas" in bindings
    assert "lastElementAcidReport" in bindings

    assert "window.McelElementRegistry" in lab
    assert "window.McelElementsCore" in lab
    assert "window.McelElementAcidTest" in lab
    assert "renderMcelElementLibraryAcidTest" in lab
    assert "mcelElementAcidRerun" in lab

    assert "global.McelElementRegistry" in registry
    assert "registerMany" in registry
    assert "serializeElement" in registry
    assert "evidencePacket" in registry

    expected_elements = [
        "element.core.app",
        "element.core.region",
        "element.core.panel",
        "element.core.toolbar",
        "element.core.field",
        "element.core.action",
        "element.core.status-feed",
        "element.core.workflow",
        "element.core.collection",
        "element.core.collection-row",
        "element.core.preview-pane",
        "element.core.mvc-model",
        "element.core.mvc-controller",
        "element.core.mvc-view",
        "element.toolkit.foundation-token",
        "element.toolkit.selection-control",
        "element.toolkit.disclosure-control",
        "element.toolkit.resize-handle",
        "element.toolkit.sort-indicator",
        "element.toolkit.filter-chip",
        "element.toolkit.command-button",
        "element.toolkit.drag-handle",
        "element.toolkit.bulk-selector",
        "element.toolkit.path-cell",
        "element.toolkit.name-cell",
        "element.toolkit.status-cell",
        "element.toolkit.risk-cell",
        "element.toolkit.datetime-cell",
        "element.toolkit.reason-cell",
        "element.toolkit.diffstat-cell",
        "element.toolkit.action-cell",
        "element.toolkit.collection-view",
        "element.toolkit.toolbar",
        "element.toolkit.split-pane",
        "element.toolkit.inspector-pane",
        "element.toolkit.preview-pane",
        "element.toolkit.status-bar",
        "element.toolkit.selection-controller",
        "element.toolkit.expansion-controller",
        "element.toolkit.column-sizing-controller",
        "element.toolkit.sort-filter-controller",
        "element.toolkit.safety-controller",
        "element.toolkit.view-resolver",
        "element.toolkit.contract-pattern",
        "element.concern.catalog",
        "element.concern.detector",
        "element.concern.boundary-map",
        "element.concern.contract-gap",
        "element.concern.mvc-split",
        "element.concern.replacement-plan",
        "element.concern.project-workbench",
        "element.concern.work-order",
        "element.concern.migration-queue",
        "element.concern.proof-plan",
        "element.resource.directory-tree",
        "element.resource.tree-viewport",
        "element.resource.tree-branch",
        "element.resource.tree-leaf",
        "element.resource.tree-expander",
        "element.resource.tree-selection-model",
        "element.resource.tree-keyboard-controller",
        "element.resource.tree-context-menu",
        "element.resource.tree-drag-drop-boundary",
        "element.resource.tree-empty-state",
        "element.resource.file-boundary",
        "element.resource.path-bar",
        "element.resource.resource-row",
        "element.resource.view-contract",
        "element.resource.selection-contract",
        "element.resource.contract-treegrid",
        "element.resource.view-mode-controller",
        "element.resource.icon-grid",
        "element.resource.details-pane",
        "element.operational.process-table",
        "element.operational.server-control",
        "element.operational.pid-action",
        "element.operational.command-surface",
        "element.network.remote-mutation-boundary",
        "element.network.credential-boundary",
        "element.network.payment-boundary",
        "element.compute.keypad",
        "element.compute.local-display",
        "element.compute.runtime-cell",
        "element.authoring.document-surface",
        "element.authoring.spreadsheet-grid",
        "element.authoring.code-editor",
        "element.authoring.website-publisher",
        "element.authoring.game-editor",
    ]
    for element_id in expected_elements:
        assert element_id in elements
        assert element_id in acid

    assert "supersedes: [\"Wunderbaum\", \"TreeView\"" in elements
    assert "element.resource.tree-viewport" in elements
    assert "element.resource.tree-branch" in elements
    assert "element.resource.tree-leaf" in elements
    assert "element.resource.tree-expander" in elements
    assert "element.resource.tree-selection-model" in elements
    assert "element.resource.tree-keyboard-controller" in elements
    assert "element.resource.tree-context-menu" in elements
    assert "element.resource.tree-drag-drop-boundary" in elements
    assert "element.resource.tree-empty-state" in elements
    assert "fromWunderbaum" in elements
    assert "node.data.fileExplorerEntry -> resource payload" in elements
    assert "explorer-sidebar" in elements
    assert "ide-project-tree" in elements
    assert "details-treegrid" in elements
    assert "miller-columns" in elements
    assert "outline-tree" in elements
    assert "accessibility-proof" in elements
    assert "extra-large-icons" in elements
    assert "tiles" in elements
    assert "content" in elements
    assert "details-pane" in elements
    assert "preview-pane" in elements
    assert "finder-gallery" in elements
    assert "finder-column-inspector" in elements
    assert "gnome-grid" in elements
    assert "gnome-list" in elements
    assert "dolphin-split-details" in elements
    assert "thunar-compact" in elements
    assert "GNOME Files view switcher" in elements
    assert "Dolphin split/details view" in elements
    assert "Thunar compact view" in elements
    assert "long-names-ellipsis-not-overlap" in elements
    assert "element.resource.view-mode-controller" in elements
    assert "element.resource.view-contract" in elements
    assert "element.resource.selection-contract" in elements
    assert "element.resource.contract-treegrid" in elements
    assert "element.core.mvc-model" in elements
    assert "element.core.mvc-controller" in elements
    assert "element.core.mvc-view" in elements
    assert "explicit-file-paths" in elements
    assert "multi-column-fields-do-not-collapse-into-title" in elements
    assert "blocked-rows-visible-not-selectable" in elements
    assert "interactive expand/collapse" in elements
    assert "resizable columns" in elements
    assert "resizable-columns-stay-inside-treegrid" in elements
    assert "selection-controls-look-like-checkboxes" in elements
    assert "resizable-columns-use-edge-grips" in elements
    assert "legible checkbox controls" in elements
    assert "keyboard resizable columns" in elements
    assert "columnWidths" in elements
    assert "title-only tree" in elements
    assert "element.resource.icon-grid" in elements
    assert "element.resource.details-pane" in elements
    assert "aria-expanded" in elements
    assert "aria-selected" in elements
    assert "activeNodeId" in elements
    assert "expandedNodeIds" in elements
    assert "visibleWindow" in elements
    assert "dropMove: \"no-submit\"" in elements
    assert "proofPolicy" in elements
    assert "serializationSchema" in elements
    assert "decoderHints" in elements
    assert "riskPolicy" in elements
    assert "stateModel" in registry
    assert "interactionModel" in registry
    assert "migrationHints" in registry
    assert "presentationModes" in registry
    assert "viewPatterns" in registry
    assert "densityModes" in registry
    assert "statefulElementCount" in registry

    assert "renderLabMissionControl" in acid
    assert "wireLabWorkbenchModes" in acid
    assert "mcel-lab-mission-control" in acid
    assert "data-mcel-lab-mission-control" in acid
    assert "data-mcel-lab-current-target" in acid
    assert "data-mcel-lab-mode" in acid
    assert "data-mcel-lab-panel" in acid
    assert "guided cockpit" in acid.lower()
    assert "Mission Control" in acid
    assert "buildDemoUi" in acid
    assert "renderResourceWorkbench" in acid
    assert "renderOperationalWorkbench" in acid
    assert "renderNetworkComputeAuthoringWorkbench" in acid
    assert "mcel-element-showcase-workbench" in acid
    assert "renderGitFileBasketTreegridLab(document, viewPanel)" in acid
    assert "data-mcel-lab-single-active-git-view-surface" in acid
    assert "data-mcel-lab-retired-generic-resource-gallery" in acid
    assert "renderResourceWorkbench(document, viewPanel" not in acid
    assert "renderOperationalWorkbench(document, viewPanel" not in acid
    assert "renderNetworkComputeAuthoringWorkbench(document, viewPanel" not in acid
    assert "contract-first MVC tree views replacing Wunderbaum" in acid
    assert "TREE_VIEW_MODES" in acid
    assert "Cycle tree views" in acid
    assert "Explorer sidebar" in acid
    assert "IDE project tree" in acid
    assert "Details treegrid" in acid
    assert "Column browser" in acid
    assert "Outline tree" in acid
    assert "Keyboard proof" in acid
    assert "Icon grid" in acid
    assert "List view" in acid
    assert "Tiles view" in acid
    assert "Content view" in acid
    assert "RESOURCE_VIEW_MENU_OPTIONS" in acid
    assert "Extra large icons" in acid
    assert "Large icons" in acid
    assert "Medium icons" in acid
    assert "Small icons" in acid
    assert "Details pane" in acid
    assert "Preview pane" in acid
    assert "View menu parity" in acid
    assert "wireResourceViewMenu" in acid
    assert "mcelResourceViewTarget" in acid
    assert "Active view:" in acid
    assert "fileExplorerViewParityReady" in acid
    assert "crossPlatformResourceViewParityReady" in acid
    assert "mac/linux parity=" in acid
    assert "Finder Gallery" in acid
    assert "Finder Columns + Inspector" in acid
    assert "GNOME Files grid" in acid
    assert "GNOME Files list" in acid
    assert "Dolphin split/details" in acid
    assert "Thunar compact" in acid
    assert "finder-gallery" in acid
    assert "finder-column-inspector" in acid
    assert "gnome-grid" in acid
    assert "gnome-list" in acid
    assert "dolphin-split-details" in acid
    assert "thunar-compact" in acid
    assert "RESOURCE_VIEW_MVC_CONTRACT" in acid
    assert "RESOURCE_VIEW_MVC_ROLES" in acid
    assert "Resource MVC contract-first file basket mock" in acid
    assert "renderResourceMvcContractMockup" in acid
    assert "resourceMvcBasketRows" in acid
    assert "MVC Model" in acid
    assert "MVC Controller" in acid
    assert "MVC View" in acid
    assert "View resolver" in acid
    assert "hierarchical-explicit-files" in acid
    assert "explicit-file-paths" in acid
    assert "directories are shortcuts" in acid
    assert "toggleDirectoryShortcut" in acid
    assert "selectAllEligible" in acid
    assert "collapseAll" in acid
    assert "resetColumnWidths" in acid
    assert "blocked rows visible, not selectable" in acid
    assert "title-only tree rejected" in acid
    assert "data-mcel-view-contract" in acid
    assert "data-mcel-selection-contract" in acid
    assert "mcelViewCapabilities" in acid
    assert "wireResourceMvcContractMockup" in acid
    assert "data-mcel-mvc-interactive" in acid
    assert "data-mcel-mvc-select" in acid
    assert "data-mcel-mvc-expander" in acid
    assert "data-mcel-resize-column" in acid
    assert "mcel-resource-mvc-select-box" in acid
    assert "mcel-resource-mvc-select-text" in acid
    assert "mcel-resource-mvc-resize-grip" in acid
    assert "aria-checked" in acid
    assert "aria-keyshortcuts" in acid
    assert "ArrowLeft" in acid
    assert "pointerdown" in acid
    assert "setResourceMvcColumnWidth" in acid
    assert "applyResourceMvcColumnPreset" in acid
    assert "selected every eligible file" in acid.lower()
    assert "resourceMvcContractReady" in acid
    assert "resourceMvcInteractiveReady" in acid
    assert "resource MVC interactive=" in acid
    assert "global.McelToolkitCore" in toolkit
    assert "TOOLKIT_VERSION" in toolkit
    assert "PRIMITIVES" in toolkit
    assert "VIEW_RECIPES" in toolkit
    assert "CONTRACT_PATTERNS" in toolkit
    assert "resolveViews" in toolkit
    assert "evaluateView" in toolkit
    assert "buildToolkitReadinessReport" in toolkit
    assert "control.selection.tristate" in toolkit
    assert "control.resize-handle" in toolkit
    assert "controller.view-resolver" in toolkit
    assert "pattern.file-basket" in toolkit
    assert "blocked-visible-not-selectable" in toolkit
    assert "titleOnlyTreeRejected" in toolkit
    assert "noOneOffControls" in toolkit
    assert "element.toolkit.foundation-token" in elements
    assert "element.toolkit.selection-control" in elements
    assert "element.toolkit.disclosure-control" in elements
    assert "element.toolkit.resize-handle" in elements
    assert "element.toolkit.path-cell" in elements
    assert "element.toolkit.status-cell" in elements
    assert "element.toolkit.risk-cell" in elements
    assert "element.toolkit.reason-cell" in elements
    assert "element.toolkit.collection-view" in elements
    assert "element.toolkit.selection-controller" in elements
    assert "element.toolkit.column-sizing-controller" in elements
    assert "element.toolkit.view-resolver" in elements
    assert "element.toolkit.contract-pattern" in elements
    assert "MCEL Toolkit Atlas" in acid
    assert "renderToolkitAtlas" in acid
    assert "mcelToolkitCore" in acid
    assert "mcel-toolkit-atlas" in acid
    assert "mcel-toolkit-primitive-deck" in acid
    assert "mcel-toolkit-state-rack" in acid
    assert "mcel-toolkit-resolver" in acid
    assert "mcel-toolkit-assembly" in acid
    assert "data-mcel-toolkit-primitive" in acid
    assert "data-mcel-toolkit-view-candidate" in acid
    assert "toolkitAtlasReady" in acid
    assert "toolkitResolverRejectsTitleOnlyTree" in acid
    assert "Toolkit primitives" in acid
    assert "Primitive state specimens" in acid
    assert "Needs → contract → visualization resolver" in acid
    assert "File-basket assembly map" in acid
    assert "Stop inventing one-off widgets" in acid
    assert "resource MVC contract=" in acid
    assert "long labels" in acid
    assert "appendTreeNode" in acid
    assert "tree-viewport" in acid
    assert "Selection model" in acid
    assert "Keyboard controller" in acid
    assert "Context menu" in acid
    assert "Drag/drop boundary" in acid
    assert "treeViewModeCount" in acid
    assert "researchedTreePatterns" in acid
    assert "hardTreePrimitiveCount" in acid
    assert "treeReplacementReady" in acid
    assert "tree replacement=" in acid
    assert "Process Table" in acid
    assert "Remote Mutation Boundary" in acid
    assert "Spreadsheet Grid" in acid
    assert "supersedesTreeView" in acid
    assert "showcaseSurfaceCount" in acid
    assert "composedUiReady" in acid
    assert "illegalNestedScrollbars" in acid
    assert "data-mcel-element-acid-root" in acid

    assert ".mcel-lab-workbench-tabs" in css
    assert ".mcel-lab-workbench-panel" in css
    assert ".mcel-lab-mission-control" in css
    assert ".mcel-lab-mission-hero" in css
    assert ".mcel-lab-mission-flow" in css
    assert ".mcel-lab-mission-map" in css
    assert ".mcel-element-acid" in css
    assert ".mcel-element-acid-grid" in css
    assert ".mcel-element-acid-card" in css
    assert ".mcel-element-showcase-workbench" in css
    assert ".mcel-element-showcase-resource-layout" in css
    assert ".mcel-element-showcase-three-column" in css
    assert ".mcel-element-tree-viewport" in css
    assert ".mcel-element-tree-node" in css
    assert ".mcel-element-tree-expander" in css
    assert ".mcel-element-tree-controller-strip" in css
    assert ".mcel-resource-tree-mode-bar" in css
    assert ".mcel-resource-tree-view-stage" in css
    assert ".mcel-resource-tree-mode--explorer-sidebar" in css
    assert ".mcel-resource-tree-viewport--ide" in css
    assert ".mcel-resource-treegrid" in css
    assert ".mcel-resource-column-browser" in css
    assert ".mcel-resource-view-menu" in css
    assert ".mcel-resource-view-menu-option" in css
    assert ".mcel-resource-icon-grid" in css
    assert ".mcel-resource-list-view" in css
    assert ".mcel-resource-tile-view" in css
    assert ".mcel-resource-content-view" in css
    assert ".mcel-resource-view-menu-status" in css
    assert ".mcel-toolkit-atlas" in css
    assert ".mcel-toolkit-score-grid" in css
    assert ".mcel-toolkit-layer-grid" in css
    assert ".mcel-toolkit-primitive-deck" in css
    assert ".mcel-toolkit-card" in css
    assert ".mcel-toolkit-state-rack" in css
    assert ".mcel-toolkit-control-sample" in css
    assert ".mcel-toolkit-resolver" in css
    assert ".mcel-toolkit-view-candidate" in css
    assert ".mcel-toolkit-assembly" in css
    assert ".mcel-resource-mvc-contract" in css
    assert ".mcel-resource-mvc-role-grid" in css
    assert ".mcel-resource-mvc-card" in css
    assert ".mcel-resource-mvc-field-strip" in css
    assert ".mcel-resource-mvc-treegrid" in css
    assert ".mcel-resource-mvc-row" in css
    assert ".mcel-resource-mvc-select" in css
    assert ".mcel-resource-mvc-select-box" in css
    assert ".mcel-resource-mvc-select-text" in css
    assert ".mcel-resource-mvc-expander" in css
    assert ".mcel-resource-mvc-resize-handle" in css
    assert ".mcel-resource-mvc-resize-grip" in css
    assert ".mcel-resource-mvc-interaction-status" in css
    assert ".mcel-resource-mvc-output" in css
    assert ".mcel-resource-mvc-resolver" in css
    assert ".mcel-resource-finder-gallery" in css
    assert ".mcel-resource-finder-column-inspector" in css
    assert ".mcel-resource-gnome-grid" in css
    assert ".mcel-resource-gnome-list" in css
    assert ".mcel-resource-dolphin-split" in css
    assert ".mcel-resource-thunar-compact" in css
    assert "[data-resource-icon-size=\"72\"]" in css
    assert ".mcel-element-showcase-details-pane" in css
    assert ".mcel-resource-tree-proof-card" in css
