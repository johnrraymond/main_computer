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
    assert "<!-- @include applications/scripts/mcel-editor.js -->" in html
    assert "<!-- @include applications/scripts/mcel-style-law.js -->" in html
    assert "<!-- @include applications/scripts/mcel-command-surface.js -->" in html
    assert "<!-- @include applications/scripts/mcel-project-store.js -->" in html
    assert "<!-- @include applications/scripts/mcel-scenarios.js -->" in html
    assert "<!-- @include applications/scripts/mcel-graph.js -->" in html
    assert "<!-- @include applications/scripts/mcel-test-harness.js -->" in html
    assert "<!-- @include applications/scripts/mcel-lab.js -->" in html
    assert (
        html.index("mcel-contract.js")
        < html.index("mcel-engine.js")
        < html.index("mcel-editor.js")
        < html.index("mcel-style-law.js")
        < html.index("mcel-command-surface.js")
        < html.index("mcel-project-store.js")
        < html.index("mcel-scenarios.js")
        < html.index("mcel-graph.js")
        < html.index("mcel-test-harness.js")
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


def test_mcel_lab_assets_define_round_trip_contract() -> None:
    app = (WEB_APP / "apps" / "mcel-lab.html").read_text(encoding="utf-8")
    contract = (WEB_APP / "scripts" / "mcel-contract.js").read_text(encoding="utf-8")
    engine = (WEB_APP / "scripts" / "mcel-engine.js").read_text(encoding="utf-8")
    editor = (WEB_APP / "scripts" / "mcel-editor.js").read_text(encoding="utf-8")
    style_law = (WEB_APP / "scripts" / "mcel-style-law.js").read_text(encoding="utf-8")
    command_surface = (WEB_APP / "scripts" / "mcel-command-surface.js").read_text(encoding="utf-8")
    project_store = (WEB_APP / "scripts" / "mcel-project-store.js").read_text(encoding="utf-8")
    graph = (WEB_APP / "scripts" / "mcel-graph.js").read_text(encoding="utf-8")
    scenarios = (WEB_APP / "scripts" / "mcel-scenarios.js").read_text(encoding="utf-8")
    graph = (WEB_APP / "scripts" / "mcel-graph.js").read_text(encoding="utf-8")
    harness = (WEB_APP / "scripts" / "mcel-test-harness.js").read_text(encoding="utf-8")
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
    assert "mcel-project-report" in app
    assert "mcel-graph-report" in app
    assert "mcel-audit-report" in app
    assert "Run Operational Audit" in app
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
    assert "McelLabCommandSurface" in command_surface
    assert "McelLabCommandSurface" in command_surface and "Semantic Command" in app
    assert "McelLabProjectStore" in project_store
    assert "never generated runtime DOM" in project_store
    assert "McelLabScenarios" in scenarios
    assert "McelLabGraph" in graph
    assert "graphFromSource" in graph
    assert "operational-audit" in graph
    assert "Neighborhood Cluster" in scenarios
    assert "Relation Hooks" in scenarios
    assert "McelLabTestHarness" in harness
    assert "editor save firewall strips generated runtime DOM" in harness
    assert "runMcelContractTests" in ui
    assert "applyMcelTraitsToSelectedSourceWidget" in ui
    assert "selectMcelSourceIndex" in ui
    assert "GrapesJS is unavailable; semantic block insertion and trait editing remain active." in app
    assert "source HTML -> runtime DOM -> serializer round trips" in style


def test_mcel_lab_has_low_debt_module_boundaries() -> None:
    contract = (WEB_APP / "scripts" / "mcel-contract.js").read_text(encoding="utf-8")
    engine = (WEB_APP / "scripts" / "mcel-engine.js").read_text(encoding="utf-8")
    style_law = (WEB_APP / "scripts" / "mcel-style-law.js").read_text(encoding="utf-8")
    command_surface = (WEB_APP / "scripts" / "mcel-command-surface.js").read_text(encoding="utf-8")
    project_store = (WEB_APP / "scripts" / "mcel-project-store.js").read_text(encoding="utf-8")
    graph = (WEB_APP / "scripts" / "mcel-graph.js").read_text(encoding="utf-8")
    ui = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    bindings = (WEB_APP / "scripts" / "dom-bindings" / "mcel-lab.js").read_text(encoding="utf-8")

    assert "const McelLabContract" in contract
    assert "const McelLabEngine" in engine
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
    assert "const McelLabStyleLaw" in style_law
    assert "const McelLabCommandSurface" in command_surface
    assert "const McelLabProjectStore" in project_store
    assert "const McelLabGraph" in graph
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
    assert html.index("mcel-scenarios.js") < html.index("mcel-graph.js") < html.index("mcel-test-harness.js")
    assert "Run Operational Audit" in app
    assert "Semantic Graph" in app
    assert "Operational Audit" in app
    assert "mcelRunAudit" in bindings
    assert "mcelGraphReport" in bindings
    assert "mcelAuditReport" in bindings
    assert 'artifactOwner: "data-mc-owner"' in contract
    assert 'contractVersion = "mcel-lab.v0.5-operational-graph"' in contract
    assert "attributes.artifactOwner" in contract
    assert 'node.setAttribute(attributes.artifactOwner, "mcel-part-manager")' in engine
    assert 'node.setAttribute(attributes.artifactReason' in engine
    assert 'element.setAttribute(attributes.artifactOwner, "mcel-runtime-builder")' in engine
    assert "const McelLabGraph" in graph
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
