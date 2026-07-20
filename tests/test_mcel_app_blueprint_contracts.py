from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_APP = ROOT / "main_computer" / "web" / "applications"
SCRIPTS = WEB_APP / "scripts"


GENERIC_LAYOUT_ELEMENTS = [
    "element.layout.identity-zone",
    "element.layout.navigation-zone",
    "element.layout.primary-work-zone",
    "element.layout.inspector-zone",
    "element.layout.evidence-zone",
    "element.layout.actions-zone",
    "element.layout.status-zone",
    "element.layout.advanced-zone",
]

INSPECTION_ELEMENTS = [
    "element.inspection.aspect-map",
    "element.inspection.aspect-panel",
    "element.inspection.blueprint-editor",
    "element.inspection.source-binding",
    "element.inspection.implementation-delta",
    "element.inspection.acid-test-result",
    "element.inspection.repair-finding",
    "element.inspection.repair-plan",
]

REFACTOR_ELEMENTS = [
    "element.refactor.annotation-map",
    "element.refactor.element-annotation",
    "element.refactor.removal-candidate",
    "element.refactor.rework-candidate",
    "element.refactor.refactor-export-packet",
]

GENERIC_ASPECTS = [
    "overview",
    "form",
    "objects",
    "workflows",
    "layout",
    "actions",
    "capabilities",
    "evidence",
    "source",
    "tests",
    "annotations",
    "findings",
    "repair",
]


BLUEPRINT_DETAIL_GROUPS = [
    "aspect-contract",
    "semantic-form-primitives",
    "layout-zone-contract",
    "support-hints",
    "export-contract",
    "raw-blueprint-json",
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


def test_generic_layout_inspection_and_refactor_elements_are_registered() -> None:
    registry_path = SCRIPTS / "mcel-element-registry.js"
    elements_path = SCRIPTS / "mcel-elements-core.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(fs.readFileSync({json.dumps(str(registry_path))}, "utf8"), sandbox, {{filename: "mcel-element-registry.js"}});
        vm.runInNewContext(fs.readFileSync({json.dumps(str(elements_path))}, "utf8"), sandbox, {{filename: "mcel-elements-core.js"}});
        const required = {json.dumps(GENERIC_LAYOUT_ELEMENTS + INSPECTION_ELEMENTS + REFACTOR_ELEMENTS)};
        const definitions = required.map((id) => sandbox.McelElementRegistry.get(id));
        process.stdout.write(JSON.stringify({{
          missing: required.filter((id, index) => !definitions[index]),
          families: Object.fromEntries(definitions.filter(Boolean).map((definition) => [definition.id, definition.family])),
          removalCandidate: sandbox.McelElementRegistry.get("element.refactor.removal-candidate"),
          exportPacket: sandbox.McelElementRegistry.get("element.refactor.refactor-export-packet"),
        }}));
        """
    )
    result = run_node_json(script)

    assert result["missing"] == []
    for element_id in GENERIC_LAYOUT_ELEMENTS:
        assert result["families"][element_id] == "layout"
    for element_id in INSPECTION_ELEMENTS:
        assert result["families"][element_id] == "inspection"
    for element_id in REFACTOR_ELEMENTS:
        assert result["families"][element_id] == "refactor"

    removal_required = result["removalCandidate"]["stateModel"]["required"]
    assert "targetSelector" in removal_required
    assert "dependencyChecks" in removal_required
    assert "sourceHints" in removal_required
    assert result["removalCandidate"]["dataModel"]["requiredDependencyChecks"] == [
        "handlers",
        "tests",
        "docs",
        "replacementPath",
        "sourceOwners",
    ]

    packet_files = result["exportPacket"]["dataModel"]["packetFiles"]
    assert "manifest.json" in packet_files
    assert "annotations.json" in packet_files
    assert "dom-snapshot.html" in packet_files
    assert "source-map.json" in packet_files
    assert "tests-to-update.json" in packet_files


def test_toolkit_patterns_reference_generic_elements_not_lab_dom_ids() -> None:
    source_path = SCRIPTS / "mcel-toolkit-core.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({json.dumps(str(source_path))}, "utf8");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(source, sandbox, {{filename: "mcel-toolkit-core.js"}});
        const api = sandbox.McelToolkitCore;
        const requiredPrimitiveIds = [
          "pattern.app-aspect-inspector",
          "pattern.mounted-app-inspection",
          "pattern.point-and-annotate",
          "pattern.refactor-export-packet",
          "pattern.self-hosting-lab"
        ];
        const requiredContracts = [
          "appAspectInspector",
          "mountedAppInspection",
          "pointAndAnnotate",
          "refactorExportPacket",
          "selfHostingLab"
        ];
        const primitives = Object.fromEntries(requiredPrimitiveIds.map((id) => [id, api.getPrimitive(id)]));
        const contracts = Object.fromEntries(requiredContracts.map((key) => [key, api.CONTRACT_PATTERNS[key]]));
        const readiness = api.buildToolkitReadinessReport();
        process.stdout.write(JSON.stringify({{
          primitives,
          contracts,
          readiness,
          appInspectorBest: api.resolveViews(api.CONTRACT_PATTERNS.appAspectInspector)[0].id,
          exportBest: api.resolveViews(api.CONTRACT_PATTERNS.refactorExportPacket)[0].id,
          selfHostingBest: api.resolveViews(api.CONTRACT_PATTERNS.selfHostingLab)[0].id,
        }}));
        """
    )
    result = run_node_json(script)

    for primitive_id, primitive in result["primitives"].items():
        assert primitive is not None, primitive_id
        assert "#mcel-lab" not in json.dumps(primitive)
        assert "data-mcel-lab" not in json.dumps(primitive)

    assert result["primitives"]["pattern.app-aspect-inspector"]["elementId"] == "element.inspection.aspect-map"
    assert result["primitives"]["pattern.point-and-annotate"]["elementId"] == "element.refactor.element-annotation"
    assert result["primitives"]["pattern.refactor-export-packet"]["elementId"] == "element.refactor.refactor-export-packet"

    assert result["contracts"]["appAspectInspector"]["requiredPrimitives"][0] == "pattern.app-aspect-inspector"
    assert "element.inspection.aspect-map" in result["contracts"]["appAspectInspector"]["requiredPrimitives"]
    assert "element.refactor.removal-candidate" in result["contracts"]["pointAndAnnotate"]["requiredPrimitives"]
    assert "remove-without-dependency-checks" in result["contracts"]["pointAndAnnotate"]["mustReject"]
    assert "live-self-overwrite" in result["contracts"]["selfHostingLab"]["mustReject"]

    assert result["readiness"]["appAspectInspectorBestView"] == "app-aspect-inspection-workbench"
    assert result["appInspectorBest"] == "app-aspect-inspection-workbench"
    assert result["exportBest"] == "app-aspect-inspection-workbench"
    assert result["selfHostingBest"] == "app-aspect-inspection-workbench"


def test_inspectable_blueprints_share_generic_aspect_model() -> None:
    blueprint_path = SCRIPTS / "mcel-app-blueprints-core.js"
    planner_path = SCRIPTS / "mcel-specimen-planner.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(fs.readFileSync({json.dumps(str(blueprint_path))}, "utf8"), sandbox, {{filename: "mcel-app-blueprints-core.js"}});
        vm.runInNewContext(fs.readFileSync({json.dumps(str(planner_path))}, "utf8"), sandbox, {{filename: "mcel-specimen-planner.js"}});
        const core = sandbox.McelAppBlueprintsCore;
        const planner = sandbox.McelSpecimenPlanner;
        const documentEditor = core.inspectableBlueprintFor("document-editor");
        const documentAlias = core.inspectableBlueprintFor("document");
        const lab = core.inspectableBlueprintFor("mcel-lab");
        const plannerBlueprints = planner.inspectableBlueprints();
        process.stdout.write(JSON.stringify({{
          coreVersion: core.BLUEPRINTS_CORE_VERSION,
          documentEditor,
          documentAlias,
          lab,
          plannerBlueprints,
          plannerSnapshot: planner.plannerSnapshot("document"),
          genericDetailGroups: core.genericDetailGroups(),
        }}));
        """
    )
    result = run_node_json(script)

    document_editor = result["documentEditor"]
    lab = result["lab"]

    assert document_editor["appId"] == "document-editor"
    assert result["documentAlias"]["appId"] == "document-editor"
    assert lab["appId"] == "mcel-lab"
    assert document_editor["aspectIds"] == GENERIC_ASPECTS
    assert lab["aspectIds"] == GENERIC_ASPECTS
    assert [aspect["id"] for aspect in document_editor["aspects"]] == GENERIC_ASPECTS
    assert [aspect["id"] for aspect in lab["aspects"]] == GENERIC_ASPECTS
    assert set(document_editor["layoutZones"]) == {
        "identity",
        "navigation",
        "primary",
        "inspector",
        "evidence",
        "actions",
        "status",
        "advanced",
    }
    assert document_editor["genericInspectionElements"] == INSPECTION_ELEMENTS
    assert lab["genericInspectionElements"] == INSPECTION_ELEMENTS
    assert document_editor["genericRefactorElements"] == REFACTOR_ELEMENTS
    assert lab["genericRefactorElements"] == REFACTOR_ELEMENTS
    assert [group["id"] for group in result["genericDetailGroups"]] == BLUEPRINT_DETAIL_GROUPS
    assert [group["id"] for group in document_editor["detailGroups"]] == BLUEPRINT_DETAIL_GROUPS
    assert [group["id"] for group in lab["detailGroups"]] == BLUEPRINT_DETAIL_GROUPS
    assert all(group["renderer"] for group in document_editor["detailGroups"])
    assert all(group["source"] for group in lab["detailGroups"])

    for blueprint in [document_editor, lab]:
        aspect_ids = set(blueprint["aspectIds"])
        assert "document-toolbar" not in aspect_ids
        assert "mcel-lab-debug" not in aspect_ids
        assert "fake-tab" not in aspect_ids

    planner_ids = {blueprint["appId"] for blueprint in result["plannerBlueprints"]}
    assert {"document-editor", "mcel-lab"} <= planner_ids
    assert result["plannerSnapshot"]["inspectableBlueprintCount"] >= 2


def test_requirements_backed_blueprints_expose_semantic_form_primitives() -> None:
    blueprint_path = SCRIPTS / "mcel-app-blueprints-core.js"
    registry_path = SCRIPTS / "mcel-requirements-registry.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(fs.readFileSync({json.dumps(str(registry_path))}, "utf8"), sandbox, {{filename: "mcel-requirements-registry.js"}});
        vm.runInNewContext(fs.readFileSync({json.dumps(str(blueprint_path))}, "utf8"), sandbox, {{filename: "mcel-app-blueprints-core.js"}});
        const blueprints = sandbox.McelAppBlueprintsCore.listInspectableAppBlueprints();
        const byApp = Object.fromEntries(blueprints.map((blueprint) => [blueprint.appId, blueprint]));
        process.stdout.write(JSON.stringify({{
          appIds: blueprints.map((blueprint) => blueprint.appId),
          codeEditor: byApp["code-editor"],
          calculator: byApp["calculator"],
          lab: byApp["mcel-lab"],
          formGroups: sandbox.McelAppBlueprintsCore.formPrimitiveGroups(byApp["code-editor"].formPrimitives),
        }}));
        """
    )
    result = run_node_json(script)

    assert {
        "calculator",
        "code-editor",
        "file-explorer",
        "git-tools",
        "mcel-lab",
        "website-builder",
    } <= set(result["appIds"])
    assert result["codeEditor"]["formPrimitiveCount"] >= 6
    assert result["calculator"]["formPrimitiveCount"] >= 6
    assert result["lab"]["requirementsContract"]["formPrimitiveCount"] >= 8
    assert "form" in result["codeEditor"]["aspectIds"]
    assert "semantic-form-primitives" in [group["id"] for group in result["codeEditor"]["detailGroups"]]
    assert "subject" in result["codeEditor"]["formPrimitiveKinds"]
    assert "work-surface" in result["codeEditor"]["formPrimitiveKinds"]
    assert "feedback" in result["codeEditor"]["formPrimitiveKinds"]
    assert "subject" in result["formGroups"]
    assert result["codeEditor"]["rootSelector"] == "#code-editor-app"
    assert result["calculator"]["route"] == "/applications/calculator"



def test_removal_and_rework_annotations_require_dependency_checks() -> None:
    blueprint_path = SCRIPTS / "mcel-app-blueprints-core.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(fs.readFileSync({json.dumps(str(blueprint_path))}, "utf8"), sandbox, {{filename: "mcel-app-blueprints-core.js"}});
        const documentEditor = sandbox.McelAppBlueprintsCore.inspectableBlueprintFor("document-editor");
        const lab = sandbox.McelAppBlueprintsCore.inspectableBlueprintFor("mcel-lab");
        process.stdout.write(JSON.stringify({{documentEditor, lab}}));
        """
    )
    result = run_node_json(script)

    for blueprint in [result["documentEditor"], result["lab"]]:
        policy = blueprint["annotationPolicy"]
        assert policy["removalOrReworkRequiresDependencyChecks"] is True
        assert policy["dependencyChecksAreRequiredBeforeDeletion"] is True
        assert policy["userIntentIsNotVerifiedFact"] is True
        assert set(policy["requiredDependencyChecks"]) == {
            "handlers",
            "tests",
            "docs",
            "sourceOwners",
            "replacementPath",
        }
        assert "element.refactor.removal-candidate" in policy["candidateElementIds"]
        assert "element.refactor.rework-candidate" in policy["candidateElementIds"]


def test_phase_one_contracts_do_not_add_visible_spec_cards_to_product_apps() -> None:
    product_apps = [
        WEB_APP / "apps" / "document.html",
        WEB_APP / "apps" / "git-tools.html",
        WEB_APP / "apps" / "code-editor.html",
    ]

    forbidden_visible_contract_markers = [
        "element.inspection.aspect-map",
        "element.refactor.refactor-export-packet",
        "pattern.app-aspect-inspector",
        "mcel-app-blueprints-core",
        "McelAppBlueprintsCore",
        "app-aspect-inspection-workbench",
    ]

    for app_file in product_apps:
        source = app_file.read_text(encoding="utf-8")
        for marker in forbidden_visible_contract_markers:
            assert marker not in source

    applications_html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    assert "<!-- @include applications/scripts/mcel-app-blueprints-core.js -->" in applications_html
    assert applications_html.index("mcel-app-blueprints-core.js") < applications_html.index("mcel-specimen-planner.js")
