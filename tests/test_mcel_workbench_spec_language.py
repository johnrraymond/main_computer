from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_APP = PROJECT_ROOT / "main_computer/web/applications"


def run_node_json(script: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_mwsl_elements_define_workbench_capability_projection_and_git_history() -> None:
    elements = (WEB_APP / "scripts" / "mcel-elements-core.js").read_text(encoding="utf-8")

    required_elements = [
        "element.workbench.specification",
        "element.app.dominant-object",
        "element.app.workflow-map",
        "element.app.capability-projection",
        "element.app.layout-slot",
        "element.app.action-hierarchy",
        "element.app.evidence-flow",
        "element.app.visual-policy",
        "element.version.git-backed-history",
        "element.version.autosave-controller",
        "element.version.revision-timeline",
        "element.version.diff-preview",
        "element.version.restore-intent",
    ]
    for element_id in required_elements:
        assert element_id in elements

    assert "MWSL app contract that binds dominant object, workflow, capability projection" in elements
    assert "no-raw-provider-dump" in elements
    assert "restore as new version" in elements
    assert "git reset --hard" in elements
    assert "commit every keystroke" in elements


def test_mwsl_toolkit_patterns_resolve_to_workbench_composition_shell() -> None:
    source_path = WEB_APP / "scripts" / "mcel-toolkit-core.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({json.dumps(str(source_path))}, "utf8");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(source, sandbox, {{filename: "mcel-toolkit-core.js"}});
        const api = sandbox.McelToolkitCore;
        const workbench = api.CONTRACT_PATTERNS.workbenchSpecification;
        const gitHistory = api.CONTRACT_PATTERNS.gitBackedDocumentHistory;
        const readiness = api.buildToolkitReadinessReport();
        const workbenchResolution = api.resolveViews(workbench)[0];
        const gitResolution = api.resolveViews(gitHistory)[0];
        process.stdout.write(JSON.stringify({{
          primitiveCount: readiness.primitiveCount,
          workbenchBestView: readiness.workbenchBestView,
          workbenchEligibleViewCount: readiness.workbenchEligibleViewCount,
          gitBackedDocumentHistoryEligibleViewCount: readiness.gitBackedDocumentHistoryEligibleViewCount,
          workbenchResolution: workbenchResolution.id,
          gitResolution: gitResolution.id,
          gitRejects: gitHistory.mustReject,
        }}));
        """
    )
    result = run_node_json(script)

    assert result["workbenchBestView"] == "workbench-composition-shell"
    assert result["workbenchResolution"] == "workbench-composition-shell"
    assert result["gitResolution"] == "workbench-composition-shell"
    assert result["workbenchEligibleViewCount"] >= 1
    assert result["gitBackedDocumentHistoryEligibleViewCount"] >= 1
    assert "raw-git-log-primary" in result["gitRejects"]
    assert "git-reset-as-restore" in result["gitRejects"]


def test_mwsl_planner_projects_git_tools_code_editor_and_document_by_dominant_object() -> None:
    source_path = WEB_APP / "scripts" / "mcel-specimen-planner.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({json.dumps(str(source_path))}, "utf8");
        const sandbox = {{console, window: {{}}}};
        sandbox.window.window = sandbox.window;
        vm.runInNewContext(source, sandbox, {{filename: "mcel-specimen-planner.js"}});
        const planner = sandbox.window.McelSpecimenPlanner;
        const git = planner.planFor("git-tools");
        const code = planner.planFor("code-editor");
        const documentPlan = planner.planFor("document");
        const snapshot = planner.plannerSnapshot("document");
        process.stdout.write(JSON.stringify({{
          version: planner.PLANNER_VERSION,
          slots: planner.WORKBENCH_LAYOUT_SLOTS,
          gitObject: git.workbenchSpec.dominantObject,
          gitAdvanced: git.workbenchSpec.layout.advanced,
          gitFindings: planner.workbenchFindingsFor(git),
          codeObject: code.workbenchSpec.dominantObject,
          codePrimaryFocus: code.workbenchSpec.visualPolicy.primaryFocus,
          docObject: documentPlan.workbenchSpec.dominantObject,
          docProvider: documentPlan.workbenchSpec.capabilityProjections[0].provider,
          docExpose: documentPlan.workbenchSpec.capabilityProjections[0].expose,
          docHidePrimary: documentPlan.workbenchSpec.capabilityProjections[0].hidePrimary,
          docLaws: Object.keys(documentPlan.workbenchSpec.laws),
          docLayout: documentPlan.workbenchSpec.layout,
          docFindings: planner.workbenchFindingsFor(documentPlan),
          workbenchSpecReady: snapshot.workbenchSpecReady,
          capabilitySummary: planner.workbenchCapabilitySummary(documentPlan),
          layoutSummary: planner.workbenchLayoutSlotSummary(documentPlan),
        }}));
        """
    )
    result = run_node_json(script)

    assert result["version"] == "0.3.0"
    assert result["slots"] == ["identity", "primary", "actions", "inspector", "evidence", "advanced", "status"]
    assert result["gitObject"] == "Repository"
    assert "ManualCommand" in result["gitAdvanced"]
    assert result["gitFindings"] == []
    assert result["codeObject"] == "SourceWorkspace"
    assert result["codePrimaryFocus"] == "SourceEditor"
    assert result["docObject"] == "Document"
    assert result["docProvider"] == "GitTools"
    assert "AutosaveStatus" in result["docExpose"]
    assert "RestoreAsNewVersion" in result["docExpose"]
    assert "ManualGitCommand" in result["docHidePrimary"]
    assert "RestorePreservesHistory" in result["docLaws"]
    assert result["docLayout"]["actions"] == ["AutosaveStatus", "CreateCheckpoint"]
    assert "GitTechnicalDetails" in result["docLayout"]["advanced"]
    assert result["docFindings"] == []
    assert result["workbenchSpecReady"] >= 3
    assert "DocumentEditor consumes GitBackedHistory from GitTools" in result["capabilitySummary"]
    assert "identity:" in result["layoutSummary"]
    assert "advanced:" in result["layoutSummary"]


def test_mcel_lab_surfaces_mwsl_workbench_projection_in_planner_panel() -> None:
    lab = (WEB_APP / "scripts" / "mcel-lab.js").read_text(encoding="utf-8")
    planner = (WEB_APP / "scripts" / "mcel-specimen-planner.js").read_text(encoding="utf-8")

    assert "MWSL workbench" in lab
    assert "Layout projection" in lab
    assert "Capability projections" in lab
    assert "Workbench findings" in lab
    assert "normalizeWorkbenchSpec" in lab
    assert "workbenchLayoutSlotSummary" in lab
    assert "workbenchCapabilitySummary" in lab

    assert 'capability: "GitBackedHistory"' in planner
    assert 'provider: "GitTools"' in planner
    assert 'consumer: "DocumentEditor"' in planner
    assert "RawGitControlsInPrimaryLayout" in planner
    assert "RestoreAsNewVersion" in planner
