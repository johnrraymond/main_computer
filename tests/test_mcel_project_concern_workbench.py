from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_APP = ROOT / "main_computer" / "web" / "applications"
SCRIPTS = WEB_APP / "scripts"


def _run_project_workbench_on_real_files() -> dict:
    toolkit = SCRIPTS / "mcel-toolkit-core.js"
    concern = SCRIPTS / "mcel-concern-core.js"
    workbench = SCRIPTS / "mcel-project-concern-workbench.js"
    files = [
        SCRIPTS / "git-tools-project-workflow.js",
        SCRIPTS / "git-tools-file-basket.js",
        SCRIPTS / "task-manager.js",
        SCRIPTS / "file-explorer.js",
        SCRIPTS / "website-builder.js",
        SCRIPTS / "chat-console.js",
        SCRIPTS / "worker.js",
    ]
    node_script = f"""
const fs = require("fs");
const vm = require("vm");
globalThis.window = globalThis;
vm.runInThisContext(fs.readFileSync({json.dumps(str(toolkit))}, "utf8"), {{filename: "mcel-toolkit-core.js"}});
vm.runInThisContext(fs.readFileSync({json.dumps(str(concern))}, "utf8"), {{filename: "mcel-concern-core.js"}});
vm.runInThisContext(fs.readFileSync({json.dumps(str(workbench))}, "utf8"), {{filename: "mcel-project-concern-workbench.js"}});
const files = {json.dumps([str(path) for path in files])}.map((path) => {{
  return {{path, text: fs.readFileSync(path, "utf8")}};
}});
const report = globalThis.McelProjectConcernWorkbench.buildProjectConcernWorkbench(files, {{
  projectId: "main_computer_test.real-workbench"
}});
console.log(JSON.stringify(report));
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_mcel_project_concern_workbench_is_loaded_between_detector_and_registry() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    workbench = (SCRIPTS / "mcel-project-concern-workbench.js").read_text(encoding="utf-8")
    elements = (SCRIPTS / "mcel-elements-core.js").read_text(encoding="utf-8")
    acid = (SCRIPTS / "mcel-element-acid-test.js").read_text(encoding="utf-8")
    css = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-project-concern-workbench.js -->" in html
    assert "<!-- @include applications/scripts/mcel-git-file-basket-treegrid-lab.js -->" in html
    assert html.index("mcel-concern-core.js") < html.index("mcel-project-concern-workbench.js") < html.index("mcel-git-file-basket-treegrid-lab.js") < html.index("mcel-element-registry.js")

    assert "global.McelProjectConcernWorkbench" in workbench
    assert "buildProjectConcernWorkbench" in workbench
    assert "PROJECT_CONTRACTS" in workbench
    assert "WORK_ORDER_TEMPLATES" in workbench
    assert "pattern.file-basket" in workbench
    assert "pattern.resource-browser" in workbench
    assert "pattern.safety-preflight" in workbench

    assert "element.concern.project-workbench" in elements
    assert "element.concern.work-order" in elements
    assert "element.concern.migration-queue" in elements
    assert "element.concern.proof-plan" in elements

    assert "renderProjectConcernWorkbench" in acid
    assert "Main Computer Project Concern Workbench" in acid
    assert "mcelProjectConcernWorkbench" in acid
    assert "renderGitFileBasketTreegridLab" in acid
    assert "firstSafeMigration" in acid

    assert ".mcel-project-concern-workbench" in css
    assert ".mcel-project-work-order-card" in css
    assert ".mcel-project-migration-queue" in css
    assert ".mcel-project-proof-plan" in css
    assert ".mcel-git-treegrid-lab" in css


def test_project_workbench_turns_real_concerns_into_ranked_work_orders() -> None:
    report = _run_project_workbench_on_real_files()

    assert report["projectId"] == "main_computer_test.real-workbench"
    assert report["detectorAvailable"] is True
    assert report["detectorReport"]["detectedConcernCount"] >= 6
    assert report["summary"]["workOrderCount"] >= 6
    assert report["summary"]["contractCount"] >= 5
    assert report["summary"]["appCount"] >= 4
    assert report["summary"]["hasFirstSafePatchForEveryHighPriority"] is True

    queue = report["migrationQueue"]
    assert queue
    assert queue[0]["rank"] == 1
    assert queue[0]["priority"] in {"critical", "high"}
    assert queue[0]["firstSafeMigration"]
    assert queue[0]["proofNeeded"]

    priorities = [item["priorityScore"] for item in queue]
    assert priorities == sorted(priorities, reverse=True)


def test_file_basket_work_order_is_a_real_migration_target() -> None:
    report = _run_project_workbench_on_real_files()
    order = next(order for order in report["workOrders"] if order["concernId"] == "concern.file-basket")

    assert order["id"] == "git-tools.file-basket"
    assert order["app"] == "git-tools"
    assert order["sourceFile"].endswith("git-tools-file-basket.js")
    assert order["implementationOwner"] == "git-tools"
    assert order["legacySurfaceIds"] == ["task-manager.file-basket"]
    assert order["ownershipStatus"] == "extracted-git-tools-boundary"
    assert "git-tools-file-basket.js" in order["ownershipNote"]
    assert order["targetContract"] == "pattern.file-basket"
    assert order["contractLabel"] == "File Basket Contract"
    assert order["priority"] in {"critical", "high"}
    assert order["firstSafeMigration"]
    assert order["migrationPhases"]
    assert order["testsNeeded"]
    assert order["lineEvidence"]

    fields = {field["id"] for field in order["contractFields"]}
    assert {"path", "status", "risk", "reason"}.issubset(fields)

    capabilities = set(order["requiredCapabilities"])
    assert "hierarchy" in capabilities
    assert "multi-column-fields" in capabilities
    assert "tri-state-selection" in capabilities
    assert "selected-output-proof" in capabilities

    toolkit = set(order["requiredToolkit"])
    assert "control.selection.tristate" in toolkit
    assert "controller.selection" in toolkit
    assert "collection.treegrid" in toolkit

    assert "title-only-tree" in order["rejectedViews"]
    assert any(not view["eligible"] for view in order["eligibleViews"])
    assert any("blocked" in item or "selected output" in item for item in order["testsNeeded"])


def test_file_basket_deprecated_task_manager_alias_resolves_to_git_tools_owner() -> None:
    report = _run_project_workbench_on_real_files()

    legacy = next(
        order
        for order in report["workOrders"]
        if "task-manager.file-basket" in order.get("legacySurfaceIds", [])
    )
    by_id = {order["id"]: order for order in report["workOrders"]}

    assert legacy["id"] == "git-tools.file-basket"
    assert legacy["app"] == "git-tools"
    assert legacy["sourceFile"].endswith("git-tools-file-basket.js")
    assert "task-manager.file-basket" not in by_id

    queue_item = next(item for item in report["migrationQueue"] if item["id"] == "git-tools.file-basket")
    assert queue_item["ownerApp"] == "git-tools"
    assert queue_item["sourceFile"].endswith("git-tools-file-basket.js")
    assert queue_item["legacySurfaceIds"] == ["task-manager.file-basket"]
    assert queue_item["firstSafeMigration"].startswith("Exercise git-tools.file-basket in an MCEL lab-only contract treegrid surface")


def test_project_workbench_produces_safe_first_patches_for_key_apps() -> None:
    report = _run_project_workbench_on_real_files()
    by_id = {order["id"]: order for order in report["workOrders"]}

    assert "git-tools.file-basket" in by_id
    assert "task-manager.file-basket" not in by_id
    assert "file-explorer.resource-browser" in by_id
    assert "website-builder.deploy-preflight" in by_id
    assert "chat-console.execution-cell" in by_id

    browser = by_id["file-explorer.resource-browser"]
    assert browser["targetContract"] == "pattern.resource-browser"
    assert browser["selectionContract"] == "single-resource-selection-with-open-preview-actions"
    assert any("ResourceBrowserModel" in step for step in browser["migrationPhases"])
    assert "layout.preview-pane" in browser["requiredToolkit"]

    preflight = by_id["website-builder.deploy-preflight"]
    assert preflight["targetContract"] == "pattern.safety-preflight"
    assert "acknowledgement-gate" in preflight["requiredCapabilities"]
    assert any("SafetyGateController" in step for step in preflight["migrationPhases"])

    execution = by_id["chat-console.execution-cell"]
    assert execution["targetContract"] == "pattern.execution-cell"
    assert "output-rendering" in execution["requiredCapabilities"]
    assert any("ExecutionCellModel" in step for step in execution["migrationPhases"])
