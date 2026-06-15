from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_APP = ROOT / "main_computer" / "web" / "applications"
SCRIPTS = WEB_APP / "scripts"


def _run_git_tools_project_workflow_node() -> dict:
    workflow = SCRIPTS / "git-tools-project-workflow.js"
    node_script = f"""
const fs = require("fs");
const vm = require("vm");
globalThis.window = globalThis;
vm.runInThisContext(fs.readFileSync({json.dumps(str(workflow))}, "utf8"), {{filename: "git-tools-project-workflow.js"}});
const actions = [
  {{id: "save_current_state", label: "Save Current State", order: 1}},
  {{
    id: "ignore_generated_files",
    label: "Ignore generated files",
    paths: ["build/cache.tmp", "logs/debug.log"],
    affected_paths: ["build/cache.tmp"],
    ignore_rules: ["build/", "logs/"],
    safe_paths: ["build/cache.tmp"],
    weight: 12,
    showRunner: true
  }},
  {{
    id: "ignore_local_environment_files",
    label: "Ignore local environment files",
    paths: [".env.local", "logs/debug.log"],
    affected_paths: [".env.local"],
    ignore_rule_groups: {{safe: [".env.local"], questionable: ["*.local"]}},
    questionable_paths: [".env.local"],
    weight: 14,
    showRunner: false
  }},
  {{id: "secrets_scan", label: "Security / Secrets", kind: "analysis"}},
  {{id: "prepare_commit_snapshot", label: "Prepare Commit Snapshot", commit_review: {{title: "Snapshot work"}}}}
];
const displayActions = globalThis.GitToolsProjectWorkflow.wizardDisplayActions(actions);
const readiness = globalThis.GitToolsProjectWorkflow.buildReadinessReport();
const lockedClassification = globalThis.GitToolsProjectWorkflow.classifyWizardStep(
  {{id: "start_tracking_real_work", label: "Start tracking real work", kind: "workflow", state: "ready"}},
  {{project: {{locked: true}}, git: {{is_git_repo: true, has_head: true}}}}
);
const evidenceClassification = globalThis.GitToolsProjectWorkflow.classifyWizardStep(
  {{id: "classify_changed_files", label: "Classify changed files", kind: "analysis"}},
  {{project: {{}}, git: {{is_git_repo: true, has_head: true}}}}
);
console.log(JSON.stringify({{
  sourceFile: globalThis.GitToolsProjectWorkflow.SOURCE_FILE,
  readiness,
  displayActionIds: displayActions.map((step) => step.id),
  displayActionLabels: displayActions.map((step) => step.label),
  mergedGitignore: displayActions.find((step) => step.label === ".gitignore review"),
  hiddenIncluded: displayActions.some((step) => step.id === "save_current_state"),
  lockedClassification,
  evidenceClassification
}}));
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_git_tools_project_workflow_module_is_loaded_before_legacy_task_manager() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    task_manager = (SCRIPTS / "task-manager.js").read_text(encoding="utf-8")
    workflow = (SCRIPTS / "git-tools-project-workflow.js").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/git-tools-project-workflow.js -->" in html
    assert html.index("git-tools-project-workflow.js") < html.index("task-manager.js")
    assert html.index("git-tools-project-workflow.js") < html.index("git-tools.js")

    assert "global.GitToolsProjectWorkflow" in workflow
    assert "project-workflow-boundary-extracted" in workflow
    assert "function wizardDisplayActions" in workflow
    assert "function classifyWizardStep" in workflow

    assert "function gitProjectWorkflowIntegration" in task_manager
    assert "GitToolsProjectWorkflow" in task_manager
    assert "wizardDisplayActions(actions" in task_manager
    assert "classifyWizardStep(step" in task_manager
    assert "const GIT_PROJECT_WIZARD_HIDDEN_ACTION_IDS" not in task_manager
    assert "const GIT_PROJECT_EVIDENCE_STEP_IDS" not in task_manager
    assert "const GIT_PROJECT_USER_ACTION_KINDS" not in task_manager


def test_git_tools_project_workflow_owns_action_queue_and_classification_policy() -> None:
    report = _run_git_tools_project_workflow_node()

    assert report["sourceFile"].endswith("git-tools-project-workflow.js")
    assert report["readiness"]["ready"] is True
    assert report["readiness"]["ownerApp"] == "git-tools"
    assert report["readiness"]["ownershipStatus"] == "project-workflow-boundary-extracted"

    assert report["hiddenIncluded"] is False
    assert report["displayActionIds"] == [
        "ignore_generated_files",
        "secrets_filter",
        "prepare_commit_snapshot",
    ]
    assert report["displayActionLabels"] == [
        ".gitignore review",
        "Review Security / Secrets",
        "Prepare Commit Snapshot",
    ]

    merged = report["mergedGitignore"]
    assert merged["paths"] == [".env.local", "build/cache.tmp", "logs/debug.log"]
    assert merged["ignore_rules"] == ["build/", "logs/", ".env.local"]
    assert merged["questionable_ignore_rules"] == ["*.local"]
    assert merged["gitignore_review_summary"]["shared_path_count"] == 1
    assert merged["showRunner"] is True

    assert report["lockedClassification"]["lane"] == "waiting_action"
    assert "Project is locked" in report["lockedClassification"]["reason"]
    assert report["evidenceClassification"]["lane"] == "evidence"
    assert report["evidenceClassification"]["showRunner"] is False
