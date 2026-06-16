from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_APP = ROOT / "main_computer" / "web" / "applications"
SCRIPTS = WEB_APP / "scripts"


def _run_contract_treegrid_node() -> dict:
    model = SCRIPTS / "mcel-file-basket-model.js"
    controller = SCRIPTS / "mcel-file-basket-controller.js"
    git_file_basket = SCRIPTS / "git-tools-file-basket.js"
    contract_view = SCRIPTS / "git-tools-file-basket-contract-view.js"
    treegrid_lab = SCRIPTS / "mcel-git-file-basket-treegrid-lab.js"
    commit_workbench = SCRIPTS / "git-tools-commit-workbench.js"
    toolkit = SCRIPTS / "mcel-toolkit-core.js"
    concern = SCRIPTS / "mcel-concern-core.js"
    project_workbench = SCRIPTS / "mcel-project-concern-workbench.js"
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
    project_files = [
        SCRIPTS / "git-tools-project-workflow.js",
        SCRIPTS / "git-tools-file-basket.js",
        SCRIPTS / "git-tools-file-basket-contract-view.js",
        SCRIPTS / "mcel-git-file-basket-treegrid-lab.js",
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
vm.runInThisContext(fs.readFileSync({json.dumps(str(controller))}, "utf8"), {{filename: "mcel-file-basket-controller.js"}});
vm.runInThisContext(fs.readFileSync({json.dumps(str(git_file_basket))}, "utf8"), {{filename: "git-tools-file-basket.js"}});
vm.runInThisContext(fs.readFileSync({json.dumps(str(contract_view))}, "utf8"), {{filename: "git-tools-file-basket-contract-view.js"}});
vm.runInThisContext(fs.readFileSync({json.dumps(str(treegrid_lab))}, "utf8"), {{filename: "mcel-git-file-basket-treegrid-lab.js"}});
vm.runInThisContext(fs.readFileSync({json.dumps(str(commit_workbench))}, "utf8"), {{filename: "git-tools-commit-workbench.js"}});

const review = {json.dumps(sample_review)};
const fileBasket = globalThis.GitToolsFileBasket.model(review);
const legacySource = globalThis.GitToolsFileBasket.treeSource(review, fileBasket);
const legacySelectedPaths = globalThis.GitToolsFileBasket.defaultSelectedPathsFromTreeSource(legacySource);
const controllerInstance = globalThis.McelFileBasketController.createFileBasketController(fileBasket, {{selectedPaths: []}});
const directorySelection = controllerInstance.apply("set-directory-selection", {{path: "src", selected: true}});
const blockedAttempt = controllerInstance.apply("set-file-selection", {{path: "src/secrets.env", selected: true}});

const rows = globalThis.GitToolsFileBasketContractView.buildContractTreegridRows(fileBasket, {{
  selectedPaths: fileBasket.defaultSelectedPaths
}});
const directoryRows = rows.filter((row) => row.kind === "directory");
const fileRows = rows.filter((row) => row.kind === "file");
const blockedRows = rows.filter((row) => row.blocked);
const rowsByPath = Object.fromEntries(rows.map((row) => [row.repoRelativePath || row.id, row]));
const directorySelectedRows = globalThis.GitToolsFileBasketContractView.buildContractTreegridRows(fileBasket, {{
  selectedPaths: directorySelection.selectedPaths
}});
const directoryContractOutput = globalThis.GitToolsFileBasketContractView.selectedOutputFromContractRows(fileBasket, directorySelectedRows);
const readiness = globalThis.GitToolsFileBasketContractView.summarizeContractTreegridReadiness(fileBasket, {{
  selectedPaths: fileBasket.defaultSelectedPaths,
  legacySelectedPaths,
  legacyRendererActive: true,
  legacyRollbackAvailable: true,
  activeReplacement: false,
  visibleRenderer: "mcel-lab-git-treegrid"
}});
const comparison = globalThis.GitToolsFileBasketContractView.compareLegacyAndContractSelection(fileBasket, legacySelectedPaths);
const basketHtml = globalThis.GitToolsFileBasket.basketHtml(review);
const contractTreegridHtml = globalThis.GitToolsFileBasketContractView.renderContractTreegridHtml(fileBasket, {{
  selectedPaths: fileBasket.defaultSelectedPaths,
  legacySelectedPaths,
  legacyRendererActive: true,
  legacyRollbackAvailable: true,
  activeReplacement: false,
  visibleRenderer: "mcel-lab-git-treegrid"
}});
const labReport = globalThis.McelGitFileBasketTreegridLab.buildInteractiveGitTreegridLabReport();

vm.runInThisContext(fs.readFileSync({json.dumps(str(toolkit))}, "utf8"), {{filename: "mcel-toolkit-core.js"}});
vm.runInThisContext(fs.readFileSync({json.dumps(str(concern))}, "utf8"), {{filename: "mcel-concern-core.js"}});
vm.runInThisContext(fs.readFileSync({json.dumps(str(project_workbench))}, "utf8"), {{filename: "mcel-project-concern-workbench.js"}});
const projectFiles = {json.dumps([str(path) for path in project_files])}.map((path) => {{
  return {{path, text: fs.readFileSync(path, "utf8")}};
}});
const projectReport = globalThis.McelProjectConcernWorkbench.buildProjectConcernWorkbench(projectFiles, {{
  projectId: "main_computer_test.contract-treegrid-lab"
}});
const order = globalThis.McelProjectConcernWorkbench.getWorkOrder(projectReport, "git-tools.file-basket");

console.log(JSON.stringify({{
  rows,
  fileRows,
  directoryRows,
  blockedRows,
  rowsByPath,
  readiness,
  comparison,
  legacySelectedPaths,
  directorySelection,
  blockedAttempt,
  directoryContractOutput,
  labReport,
  basketHtmlIncludesContractTreegrid: basketHtml.includes("data-git-commit-contract-treegrid"),
  basketHtmlRendererContract: basketHtml.includes('data-git-commit-file-basket-renderer="contract-treegrid"'),
  basketHtmlRendererLegacy: basketHtml.includes('data-git-commit-file-basket-renderer="legacy-wunderbaum"'),
  basketHtmlIncludesLegacyRollback: basketHtml.includes("data-git-commit-legacy-tree-rollback"),
  basketHtmlIncludesActiveLegacyWunderbaum: basketHtml.includes('data-git-commit-legacy-tree-active="true"'),
  contractHtmlIncludesResizeHandles: contractTreegridHtml.includes("data-git-commit-contract-resize-handle"),
  contractHtmlIncludesColumnVars: contractTreegridHtml.includes("--git-treegrid-path-col"),
  contractHtmlIncludesIndentSpacer: contractTreegridHtml.includes("git-project-contract-treegrid-indent"),
  commitWorkbenchUsesContractInit: typeof globalThis.GitToolsCommitWorkbench.gitProjectCommitInitializeContractTreegrid === "function",
  order
}}));
"""
    result = subprocess.run(["node", "-e", node_script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def test_git_tools_file_basket_uses_legacy_renderer_while_lab_mounts_treegrid() -> None:
    html = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    contract_view = (SCRIPTS / "git-tools-file-basket-contract-view.js").read_text(encoding="utf-8")
    git_file_basket = (SCRIPTS / "git-tools-file-basket.js").read_text(encoding="utf-8")
    treegrid_lab = (SCRIPTS / "mcel-git-file-basket-treegrid-lab.js").read_text(encoding="utf-8")
    acid = (SCRIPTS / "mcel-element-acid-test.js").read_text(encoding="utf-8")
    css = (WEB_APP / "styles" / "mcel-lab.css").read_text(encoding="utf-8")

    assert "<!-- @include applications/scripts/git-tools-file-basket-contract-view.js -->" in html
    assert "<!-- @include applications/scripts/mcel-git-file-basket-treegrid-lab.js -->" in html
    assert html.index("git-tools-file-basket-contract-view.js") < html.index("mcel-project-concern-workbench.js") < html.index("mcel-git-file-basket-treegrid-lab.js") < html.index("mcel-element-acid-test.js")

    assert "global.GitToolsFileBasketContractView" in contract_view
    assert "buildContractTreegridRows" in contract_view
    assert "summarizeContractTreegridReadiness" in contract_view
    assert "compareLegacyAndContractSelection" in contract_view
    assert "initializeContractTreegrid" in contract_view
    assert "refreshTreegridVisibility" in contract_view
    assert "setDirectoryExpanded" in contract_view
    assert "setTreegridColumnWidth" in contract_view
    assert "data-git-commit-contract-resize-handle" in contract_view
    assert 'columnSizingController: "controller.column-sizing"' in contract_view
    assert 'data-git-commit-contract-treegrid-active="${activeReplacement ? "true" : "false"}"' in contract_view
    assert "activeReplacement: options.activeReplacement === true" in contract_view

    assert 'const renderer = "legacy-wunderbaum"' in git_file_basket
    assert 'const contractTreegridHtml = "";' in git_file_basket
    assert "data-git-commit-legacy-tree-active" in git_file_basket

    assert "global.McelGitFileBasketTreegridLab" in treegrid_lab
    assert "renderInteractiveGitTreegridLab" in treegrid_lab
    assert "buildInteractiveGitTreegridLabReport" in treegrid_lab
    assert "buildViewModeCatalog" in treegrid_lab
    assert "data-mcel-git-treegrid-view-mode-option" in treegrid_lab
    assert "column-browser-inspector" in treegrid_lab
    assert "plain-tree-primary" in treegrid_lab
    assert "replacementGate: \"deferred-until-interactive-lab-proof-passes\"" in treegrid_lab
    assert "renderGitFileBasketTreegridLab" in acid

    assert ".mcel-git-treegrid-lab" in css
    assert '.mcel-git-treegrid-lab .git-project-contract-treegrid input[type="checkbox"]' in css
    assert "flex: 0 0 14px" in css
    assert "width: 14px" in css
    assert "padding: 0" in css
    assert ".git-project-contract-treegrid-resize-handle" in (WEB_APP / "styles" / "git-tools.css").read_text(encoding="utf-8")
    assert "var(--git-treegrid-path-col" in css
    assert ".mcel-git-treegrid-lab-side" in css
    assert ".mcel-git-treegrid-view-mode-switcher" in css
    assert ".mcel-git-view-column-browser" in css
    assert ".mcel-git-view-icon-grid" in css


def test_contract_treegrid_rows_preserve_contract_selection_but_are_not_live_git_renderer() -> None:
    report = _run_contract_treegrid_node()

    assert report["basketHtmlIncludesContractTreegrid"] is False
    assert report["basketHtmlRendererContract"] is False
    assert report["basketHtmlRendererLegacy"] is True
    assert report["basketHtmlIncludesLegacyRollback"] is False
    assert report["basketHtmlIncludesActiveLegacyWunderbaum"] is True
    assert report["commitWorkbenchUsesContractInit"] is True
    assert report["contractHtmlIncludesResizeHandles"] is True
    assert report["contractHtmlIncludesColumnVars"] is True
    assert report["contractHtmlIncludesIndentSpacer"] is True

    readiness = report["readiness"]
    assert readiness["ready"] is True
    assert readiness["activeReplacement"] is False
    assert readiness["visibleRenderer"] == "mcel-lab-git-treegrid"
    assert readiness["legacyRendererActive"] is True
    assert readiness["legacyRollbackAvailable"] is True
    assert readiness["treegridEligible"] is True
    assert readiness["titleOnlyTreeRejected"] is True
    assert readiness["selectedOutputMatchesLegacy"] is True
    assert readiness["blockedRowsVisible"] is True
    assert readiness["blockedRowsSelectable"] is False
    assert readiness["directorySelectionControllerOwned"] is True

    assert report["comparison"]["matches"] is True
    assert report["legacySelectedPaths"] == ["src/app.js"]

    file_rows = report["fileRows"]
    assert {row["repoRelativePath"] for row in file_rows} == {
        "src/app.js",
        "src/feature/needs-review.js",
        "src/lib/util.js",
        "src/secrets.env",
        "build/cache.tmp",
    }
    assert all(row["cells"]["path"]["type"] == "path" for row in file_rows)
    assert all(row["cells"]["status"]["type"] == "enum" for row in file_rows)
    assert all(row["cells"]["risk"]["type"] == "risk" for row in file_rows)
    assert all(row["cells"]["reason"]["type"] == "text" for row in file_rows)

    blocked = {row["repoRelativePath"]: row for row in report["blockedRows"]}
    assert {"src/secrets.env", "build/cache.tmp"} <= set(blocked)
    assert blocked["src/secrets.env"]["visible"] is True
    assert blocked["src/secrets.env"]["selectable"] is False
    assert blocked["src/secrets.env"]["selectionState"] == "blocked"

    assert report["directorySelection"]["selectedPaths"] == [
        "src/app.js",
        "src/feature/needs-review.js",
        "src/lib/util.js",
    ]
    assert report["directoryContractOutput"] == report["directorySelection"]["selectedPaths"]
    assert report["blockedAttempt"]["ok"] is False
    assert report["blockedAttempt"]["selectedPaths"] == report["directorySelection"]["selectedPaths"]


def test_mcel_lab_exercises_git_shaped_treegrid_before_replacement() -> None:
    report = _run_contract_treegrid_node()
    lab = report["labReport"]

    assert lab["ready"] is True
    assert lab["targetConcern"] == "git-tools.file-basket"
    assert lab["activeInGitTools"] is False
    assert lab["gitToolsRenderer"] == "legacy-wunderbaum"
    assert lab["visibleRenderer"] == "mcel-lab-git-treegrid"
    assert lab["replacementGate"] == "deferred-until-interactive-lab-proof-passes"
    assert lab["defaultViewMode"] == "contract-treegrid"
    assert {"contract-treegrid", "details-tree"} <= set(lab["eligibleViewModeIds"])
    assert {"compact-audit-list", "data-table", "column-browser-inspector", "title-only-tree", "plain-tree-primary", "icon-grid-primary"} <= set(lab["rejectedViewModeIds"])
    view_modes = {mode["id"]: mode for mode in lab["viewModes"]}
    assert view_modes["contract-treegrid"]["interactive"] is True
    assert view_modes["details-tree"]["interactive"] is True
    assert view_modes["column-browser-inspector"]["status"] == "rejected"
    assert "tri-state-selection" in view_modes["column-browser-inspector"]["missingCapabilities"]
    assert view_modes["plain-tree-primary"]["status"] == "rejected"
    assert lab["readiness"]["activeReplacement"] is False
    assert lab["readiness"]["legacyRendererActive"] is True

    proof = lab["proofChecks"]
    assert proof["legacyGitTreeStillActive"] is True
    assert proof["labOnlyTreegrid"] is True
    assert proof["activeReplacement"] is False
    assert proof["rowsHaveRepoRelativeIdentity"] is True
    assert proof["pathCellsStructured"] is True
    assert proof["typedFieldsStructured"] is True
    assert proof["blockedRowsVisible"] is True
    assert proof["blockedRowsSelectable"] is False
    assert proof["selectedBlockedPaths"] == []
    assert proof["directorySelectionMatchesContractOutput"] is True
    assert proof["directorySelectionUsesSelectableDescendants"] is True
    assert proof["disclosureExpansionPrepared"] is True
    assert proof["columnResizePrepared"] is True
    assert proof["viewModeOptionsAvailable"] is True
    assert proof["eligibleViewModesIncludeTreegrid"] is True
    assert proof["rejectedViewModesPreserved"] is True
    assert proof["selectedOutputMatchesLegacy"] is True
    assert proof["titleOnlyTreeRejected"] is True

    assert "runtime/git-tools/.env.local" in lab["scenarios"]["probes"]["blocked"]
    assert lab["scenarios"]["blockedAttempt"]["ok"] is False
    assert lab["directoryContractOutput"] == lab["scenarios"]["directorySelection"]["selectedPaths"]


def test_project_workbench_marks_git_file_basket_as_interactive_lab_target() -> None:
    report = _run_contract_treegrid_node()
    order = report["order"]

    assert order["id"] == "git-tools.file-basket"
    assert order["implementationStatus"]["status"] == "interactive-lab-active"
    assert order["implementationStatus"]["module"] == "McelGitFileBasketTreegridLab"
    assert any("legacy-wunderbaum" in proof for proof in order["implementationStatus"]["proof"])
    assert any("replacement deferred" in proof for proof in order["implementationStatus"]["proof"])
    assert "mcel lab-only contract treegrid surface" in order["firstSafeMigration"][0].lower()
    assert any("interactive MCEL lab surface" in phase for phase in order["migrationPhases"])
