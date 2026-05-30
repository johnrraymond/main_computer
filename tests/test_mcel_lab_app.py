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
    assert "<!-- @include applications/scripts/mcel-ops-runner.js -->" in html
    assert "<!-- @include applications/scripts/mcel-acid-tests.js -->" in html
    assert "<!-- @include applications/scripts/mcel-test-harness.js -->" in html
    assert "<!-- @include applications/scripts/mcel-supervisor.js -->" in html
    assert "<!-- @include applications/scripts/mcel-kernel.js -->" in html
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
        < html.index("mcel-ops-runner.js")
        < html.index("mcel-acid-tests.js")
        < html.index("mcel-test-harness.js")
        < html.index("mcel-supervisor.js")
        < html.index("mcel-kernel.js")
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


def test_mcel_lab_has_low_debt_module_boundaries() -> None:
    contract = (WEB_APP / "scripts" / "mcel-contract.js").read_text(encoding="utf-8")
    engine = (WEB_APP / "scripts" / "mcel-engine.js").read_text(encoding="utf-8")
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
    assert 'contractVersion = "mcel-lab.v0.5-operational-graph"' in contract
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
        "mcel-editor.js",
        "mcel-style-law.js",
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
    assert html.index("mcel-test-harness.js") < html.index("mcel-supervisor.js") < html.index("mcel-kernel.js") < html.index("mcel-lab.js")
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
    assert html.index("mcel-supervisor.js") < html.index("mcel-kernel.js") < html.index("mcel-lab.js")
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
    assert "Primary surface stays focused on source, semantic editor, and runtime output." in app
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
