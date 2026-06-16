from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_APP = ROOT / "main_computer" / "web" / "applications"
SCRIPTS = WEB_APP / "scripts"


def _run_file_basket_node() -> dict:
    model = SCRIPTS / "mcel-file-basket-model.js"
    workbench = SCRIPTS / "mcel-project-concern-workbench.js"
    toolkit = SCRIPTS / "mcel-toolkit-core.js"
    concern = SCRIPTS / "mcel-concern-core.js"
    sample_review = {
        "candidate_groups": {
            "selected_by_default": [
                {
                    "path": "main_computer/web/applications/scripts/task-manager.js",
                    "status": "modified",
                    "classifications": ["source"],
                    "modified": "today",
                }
            ],
            "review_before_selecting": [
                {
                    "path": "tests/test_mcel_file_basket_model.py",
                    "status": "untracked",
                    "risk": "review",
                    "reason": "new contract proof",
                }
            ],
            "blocked_possible_secrets": [
                {
                    "path": "runtime/secrets.env",
                    "status": "untracked",
                    "risk": "blocked",
                    "reason": "secret-looking runtime file",
                    "blocking_security_findings_count": 1,
                }
            ],
            "excluded_generated_runtime": [
                {
                    "path": "runtime/cache/task-manager.tmp",
                    "status": "untracked",
                    "reason": "generated runtime",
                }
            ],
        }
    }
    files = [
        SCRIPTS / "git-tools-project-workflow.js",
        SCRIPTS / "git-tools-file-basket.js",
        SCRIPTS / "git-tools-commit-workbench.js",
        SCRIPTS / "git-tools-legacy-ui-bridge.js",
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
vm.runInThisContext(fs.readFileSync({json.dumps(str(model))}, "utf8"), {{filename: "mcel-file-basket-model.js"}});
const review = {json.dumps(sample_review)};
const fileBasket = globalThis.McelFileBasketModel.buildFileBasketModel(review, {{surfaceId: "git-tools.file-basket"}});
const rootSelection = globalThis.McelFileBasketModel.toggleDirectorySelection(fileBasket, [], "");
const srcSelection = globalThis.McelFileBasketModel.toggleDirectorySelection(fileBasket, [], "main_computer/web/applications/scripts");
const blockedSelection = globalThis.McelFileBasketModel.toggleFileSelection(fileBasket, [], "runtime/secrets.env", true);
const defaultSummary = globalThis.McelFileBasketModel.selectionSummary(fileBasket, fileBasket.defaultSelectedPaths);
const rootSummary = globalThis.McelFileBasketModel.selectionSummary(fileBasket, rootSelection);
const titleOnly = globalThis.McelFileBasketModel.resolveViewEligibility(fileBasket, "title-only-tree");
const treegrid = globalThis.McelFileBasketModel.resolveViewEligibility(fileBasket, "contract-treegrid");
const readiness = globalThis.McelFileBasketModel.buildReadinessReport();

vm.runInThisContext(fs.readFileSync({json.dumps(str(toolkit))}, "utf8"), {{filename: "mcel-toolkit-core.js"}});
vm.runInThisContext(fs.readFileSync({json.dumps(str(concern))}, "utf8"), {{filename: "mcel-concern-core.js"}});
vm.runInThisContext(fs.readFileSync({json.dumps(str(workbench))}, "utf8"), {{filename: "mcel-project-concern-workbench.js"}});
const projectFiles = {json.dumps([str(path) for path in files])}.map((path) => {{
  return {{path, text: fs.readFileSync(path, "utf8")}};
}});
const projectWorkbench = globalThis.McelProjectConcernWorkbench.buildProjectConcernWorkbench(projectFiles, {{
  projectId: "main_computer_test.file-basket-model"
}});
const fileBasketOrder = globalThis.McelProjectConcernWorkbench.getWorkOrder(projectWorkbench, "git-tools.file-basket");
const legacyFileBasketOrder = globalThis.McelProjectConcernWorkbench.getWorkOrder(projectWorkbench, "task-manager.file-basket");

console.log(JSON.stringify({{
  fileBasket,
  rootSelection,
  srcSelection,
  blockedSelection,
  defaultSummary,
  rootSummary,
  titleOnly,
  treegrid,
  readiness,
  fileBasketOrder,
  legacyFileBasketOrder
}}));
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def _run_task_manager_file_basket_integration_node() -> dict:
    model = SCRIPTS / "mcel-file-basket-model.js"
    legacy_bridge = SCRIPTS / "git-tools-legacy-ui-bridge.js"
    commit_workbench = SCRIPTS / "git-tools-commit-workbench.js"
    git_file_basket = SCRIPTS / "git-tools-file-basket.js"
    sample_review = {
        "candidate_groups": {
            "selected_by_default": [
                {
                    "path": "src/app.js",
                    "status": "modified",
                    "classifications": ["source", "ui"],
                    "risk": "clean",
                    "reason": "safe source file",
                }
            ],
            "review_before_selecting": [
                {
                    "path": "src/feature/needs-review.js",
                    "status": "untracked",
                    "classifications": ["source"],
                    "risk": "review",
                    "reason": "new feature file",
                },
                {
                    "path": "src/lib/util.js",
                    "status": "modified",
                    "classifications": ["source"],
                    "risk": "review",
                    "reason": "utility change",
                },
            ],
            "blocked_possible_secrets": [
                {
                    "path": "src/secrets.env",
                    "status": "untracked",
                    "risk": "blocked",
                    "reason": "secret-looking env file",
                    "blocking_security_findings_count": 1,
                }
            ],
            "excluded_generated_runtime": [
                {
                    "path": "build/cache.tmp",
                    "status": "untracked",
                    "reason": "generated runtime",
                }
            ],
        }
    }
    node_script = f"""
const fs = require("fs");
const vm = require("vm");
globalThis.window = globalThis;
globalThis.escapeHtml = (value) => String(value ?? "")
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;")
  .replace(/'/g, "&#039;");
vm.runInThisContext(fs.readFileSync({json.dumps(str(model))}, "utf8"), {{filename: "mcel-file-basket-model.js"}});
vm.runInThisContext(fs.readFileSync({json.dumps(str(git_file_basket))}, "utf8"), {{filename: "git-tools-file-basket.js"}});

vm.runInThisContext(fs.readFileSync({json.dumps(str(commit_workbench))}, "utf8"), {{filename: "git-tools-commit-workbench.js"}});

const review = {json.dumps(sample_review)};
const modelFromTaskManager = gitProjectCommitFileBasketModel(review);
const treeSource = gitProjectCommitTreeSource(review, modelFromTaskManager);
const basketHtml = gitProjectCommitBasketHtml(review);
const treeSourcePaths = [];
const blockedTreeRows = [];
const selectedTreePaths = [];
function visit(nodes) {{
  (nodes || []).forEach((node) => {{
    const data = node.data || {{}};
    if (data.kind === "file") {{
      treeSourcePaths.push(data.path);
      if (data.blocked || data.selectable === false) blockedTreeRows.push({{
        path: data.path,
        checkbox: node.checkbox,
        unselectable: node.unselectable,
        blockedReason: data.blockedReason
      }});
      if (node.selected && data.selectable !== false) selectedTreePaths.push(data.path);
    }}
    if (Array.isArray(node.children)) visit(node.children);
  }});
}}
visit(treeSource);

const directoryShortcut = globalThis.McelFileBasketModel.toggleDirectorySelection(modelFromTaskManager, [], "src");
const blockedAttempt = globalThis.McelFileBasketModel.toggleFileSelection(modelFromTaskManager, [], "src/secrets.env", true);
const workbench = {{
  querySelector(selector) {{
    if (selector === "[data-git-commit-file-basket-model]") {{
      return {{value: JSON.stringify(modelFromTaskManager)}};
    }}
    if (selector === "[data-git-commit-tree-source]") {{
      return {{value: JSON.stringify(treeSource)}};
    }}
    return null;
  }}
}};
const adapterSelected = gitProjectCommitAdapterSelectedOutput(workbench, [
  "src/app.js",
  "src/secrets.env",
  "build/cache.tmp",
  "src/feature/needs-review.js"
]);
const adapterReport = gitProjectCommitSelectionAdapterReport(workbench, [
  "src/app.js",
  "src/secrets.env",
  "build/cache.tmp"
]);
const reviewCandidatePaths = gitProjectCommitReviewCandidatePaths(review);

console.log(JSON.stringify({{
  model: modelFromTaskManager,
  treeSourcePaths,
  blockedTreeRows,
  selectedTreePaths: selectedTreePaths.sort((a, b) => a.localeCompare(b)),
  defaultSelectedPaths: modelFromTaskManager.defaultSelectedPaths,
  directoryShortcut,
  blockedAttempt,
  adapterSelected,
  adapterReport,
  reviewCandidatePaths,
  basketHtmlIncludesModel: basketHtml.includes("data-git-commit-file-basket-model"),
  basketHtmlModelReady: basketHtml.includes('data-git-commit-file-basket-model-ready="true"')
}}));
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_mcel_file_basket_model_is_loaded_before_task_manager_and_workbench() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    model = (SCRIPTS / "mcel-file-basket-model.js").read_text(encoding="utf-8")
    task_manager = (SCRIPTS / "task-manager.js").read_text(encoding="utf-8")
    legacy_bridge = (SCRIPTS / "git-tools-legacy-ui-bridge.js").read_text(encoding="utf-8")
    commit_workbench = (SCRIPTS / "git-tools-commit-workbench.js").read_text(encoding="utf-8")
    git_file_basket = (SCRIPTS / "git-tools-file-basket.js").read_text(encoding="utf-8")
    contract_view = (SCRIPTS / "git-tools-file-basket-contract-view.js").read_text(encoding="utf-8")
    elements = (SCRIPTS / "mcel-elements-core.js").read_text(encoding="utf-8")
    acid = (SCRIPTS / "mcel-element-acid-test.js").read_text(encoding="utf-8")
    css = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/mcel-file-basket-model.js -->" in html
    assert "<!-- @include applications/scripts/git-tools-project-workflow.js -->" in html
    assert "<!-- @include applications/scripts/git-tools-file-basket.js -->" in html
    assert "<!-- @include applications/scripts/git-tools-file-basket-contract-view.js -->" in html
    assert html.index("mcel-file-basket-model.js") < html.index("git-tools-project-workflow.js") < html.index("git-tools-file-basket.js") < html.index("git-tools-file-basket-contract-view.js") < html.index("git-tools-commit-workbench.js") < html.index("git-tools-legacy-ui-bridge.js") < html.index("task-manager.js")
    assert html.index("mcel-file-basket-model.js") < html.index("mcel-project-concern-workbench.js")

    assert "global.McelFileBasketModel" in model
    assert "buildFileBasketModel" in model
    assert "toggleDirectorySelection" in model
    assert "blockedRowsVisible" in model
    assert "selectedFilesAreSourceOfTruth" in model
    assert "title-only-tree" in model

    assert "global.GitToolsFileBasket" in git_file_basket
    assert "sourceFile: SOURCE_FILE" in git_file_basket
    assert "function treeSource" in git_file_basket
    assert "function selectedFilesFromWorkbench" in git_file_basket
    assert "data-git-commit-file-basket-model" in git_file_basket
    assert "global.GitToolsFileBasketContractView" in contract_view
    assert "buildContractTreegridRows" in contract_view
    assert "initializeContractTreegrid" in contract_view
    assert "function gitProjectCommitFileBasketModel" in commit_workbench
    assert "GitToolsFileBasket" in commit_workbench
    assert "adapter.buildFileBasketModel" not in task_manager
    assert "adapter.buildFileBasketModel" not in legacy_bridge
    assert "adapter.buildFileBasketModel" not in commit_workbench
    assert "function gitProjectCommitTreeSourceFromModel" in commit_workbench
    assert "gitProjectCommitAdapterSelectedOutput" in commit_workbench
    assert "gitProjectCommitSelectionAdapterReport" in commit_workbench
    assert "GitToolsLegacyUiBridge" in legacy_bridge

    assert "element.resource.file-basket-model" in elements
    assert "File Basket Model Adapter" in elements
    assert "renderFileBasketModelProof" in acid
    assert "Git Tools File Basket now has a pure MCEL model adapter." in acid
    assert ".mcel-file-basket-model-proof" in css


def test_file_basket_model_preserves_fields_hierarchy_and_selection_contract() -> None:
    report = _run_file_basket_node()
    model = report["fileBasket"]

    assert report["readiness"]["ready"] is True
    assert model["contractId"] == "pattern.file-basket"
    assert model["surfaceId"] == "git-tools.file-basket"
    assert model["canonicalSurfaceId"] == "git-tools.file-basket"
    assert model["ownerApp"] == "git-tools"
    assert model["legacySurfaceIds"] == ["task-manager.file-basket"]
    assert model["ownershipStatus"] == "model-adapter-ready"

    fields = {field["id"] for field in model["fields"]}
    assert {"path", "status", "bucket", "risk", "reason", "modified", "blockedReason"} <= fields

    assert model["stats"]["total"] == 4
    assert model["stats"]["selectable"] == 2
    assert model["stats"]["blocked"] == 2
    assert model["selectablePaths"] == [
        "main_computer/web/applications/scripts/task-manager.js",
        "tests/test_mcel_file_basket_model.py",
    ]
    assert model["defaultSelectedPaths"] == ["main_computer/web/applications/scripts/task-manager.js"]
    assert set(model["blockedPaths"]) == {"runtime/cache/task-manager.tmp", "runtime/secrets.env"}

    blocked_rows = [row for row in model["rows"] if not row["selectable"]]
    assert len(blocked_rows) == 2
    assert all(row["blockedReason"] for row in blocked_rows)

    assert report["rootSelection"] == model["selectablePaths"]
    assert report["srcSelection"] == ["main_computer/web/applications/scripts/task-manager.js"]
    assert report["blockedSelection"] == []
    assert report["rootSummary"]["selectedBlocked"] == 0
    assert report["defaultSummary"]["selected"] == 1

    assert report["treegrid"]["eligible"] is True
    assert report["titleOnly"]["eligible"] is False
    assert "multi-column-fields" in report["titleOnly"]["missingCapabilities"]
    assert model["viewContract"]["titleOnlyTreeRejected"] is True


def test_project_workbench_marks_file_basket_first_safe_patch_as_backed_by_adapter() -> None:
    report = _run_file_basket_node()
    order = report["fileBasketOrder"]

    assert order["id"] == "git-tools.file-basket"
    assert report["legacyFileBasketOrder"]["id"] == "git-tools.file-basket"
    assert order["app"] == "git-tools"
    assert order["sourceFile"].endswith("git-tools-file-basket.js")
    assert order["legacySurfaceIds"] == ["task-manager.file-basket"]
    assert order["ownershipStatus"] == "extracted-git-tools-boundary"
    assert order["targetContract"] == "pattern.file-basket"
    assert order["implementationStatus"]["status"] == "adapter-ready"
    assert order["implementationStatus"]["firstSafePatchBacked"] is True
    assert order["implementationStatus"]["module"] == "McelFileBasketModel"
    assert any("title-only tree rejected" in proof for proof in order["implementationStatus"]["proof"])

    assert "MCEL lab-only contract treegrid surface" in order["firstSafeMigration"][0]
    assert any("FileBasketModel adapter" in step for step in order["migrationPhases"])
    assert "blocked rows visible but not selectable" in order["safetyContract"]


def test_task_manager_file_basket_uses_adapter_for_tree_source_and_selected_output() -> None:
    report = _run_task_manager_file_basket_integration_node()
    model = report["model"]

    assert model["contractId"] == "pattern.file-basket"
    assert model["surfaceId"] == "git-tools.file-basket"
    assert model["canonicalSurfaceId"] == "git-tools.file-basket"
    assert model["ownerApp"] == "git-tools"
    assert model["legacySurfaceIds"] == ["task-manager.file-basket"]
    assert model["ownershipStatus"] == "extracted-git-tools-boundary"
    assert report["basketHtmlIncludesModel"] is True
    assert report["basketHtmlModelReady"] is True

    assert set(report["treeSourcePaths"]) == {
        "src/app.js",
        "src/feature/needs-review.js",
        "src/lib/util.js",
        "src/secrets.env",
        "build/cache.tmp",
    }
    assert report["selectedTreePaths"] == report["defaultSelectedPaths"] == ["src/app.js"]

    blocked = {row["path"]: row for row in report["blockedTreeRows"]}
    assert set(blocked) == {"build/cache.tmp", "src/secrets.env"}
    assert blocked["src/secrets.env"]["checkbox"] is False
    assert blocked["src/secrets.env"]["unselectable"] is True
    assert blocked["src/secrets.env"]["blockedReason"]

    assert report["directoryShortcut"] == [
        "src/app.js",
        "src/feature/needs-review.js",
        "src/lib/util.js",
    ]
    assert report["blockedAttempt"] == []
    assert report["adapterSelected"] == ["src/app.js", "src/feature/needs-review.js"]
    assert report["adapterReport"]["enabled"] is True
    assert report["adapterReport"]["selectedPaths"] == ["src/app.js"]
    assert report["adapterReport"]["summary"]["selectedBlocked"] == 0
    assert set(report["adapterReport"]["summary"]["invalidSelectedPaths"]) == {"build/cache.tmp", "src/secrets.env"}

    assert report["reviewCandidatePaths"] == [
        "build/cache.tmp",
        "src/app.js",
        "src/feature/needs-review.js",
        "src/lib/util.js",
        "src/secrets.env",
    ]

    rows_by_path = {row["path"]: row for row in model["rows"]}
    assert rows_by_path["src/app.js"]["classifications"] == ["source", "ui"]
    assert rows_by_path["src/secrets.env"]["selectable"] is False
    assert rows_by_path["src/secrets.env"]["blockedReason"]
