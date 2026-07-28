from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from main_computer.mcel_node_runtime import resolve_node_executable


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
REGISTRY = SCRIPTS / "mcel-domain-adapter-registry.js"
ADAPTER = SCRIPTS / "code-editor-semantic-adapter.js"
TOOLKIT = SCRIPTS / "mcel-semantic-adapter-toolkit.js"
REQUIREMENTS = SCRIPTS / "mcel-requirements-registry.js"
SHELL = ROOT / "main_computer" / "web" / "applications.html"


def run_node_json(script: str) -> dict:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; Code Editor semantic-adapter tests cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def adapter_prelude(body: str) -> str:
    return textwrap.dedent(
        f"""
        const registry = require({json.dumps(str(REGISTRY))});
        const toolkit = require({json.dumps(str(TOOLKIT))});
        const adapter = require({json.dumps(str(ADAPTER))});
        {body}
        """
    )


def test_code_editor_adapter_loads_after_toolkit_and_before_truth_consumers() -> None:
    shell = SHELL.read_text(encoding="utf-8")
    registry_include = "<!-- @include applications/scripts/mcel-domain-adapter-registry.js -->"
    toolkit_include = "<!-- @include applications/scripts/mcel-semantic-adapter-toolkit.js -->"
    adapter_include = "<!-- @include applications/scripts/code-editor-semantic-adapter.js -->"
    truth_include = "<!-- @include applications/scripts/mcel-app-truth-gate.js -->"
    planner_include = "<!-- @include applications/scripts/mcel-specimen-planner.js -->"

    for include in (registry_include, toolkit_include, adapter_include, truth_include, planner_include):
        assert include in shell

    assert shell.index(registry_include) < shell.index(toolkit_include)
    assert shell.index(toolkit_include) < shell.index(adapter_include)
    assert shell.index(adapter_include) < shell.index(truth_include)
    assert shell.index(adapter_include) < shell.index(planner_include)


def test_code_editor_adapter_proves_source_safe_semantic_runtime_scope() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            const readiness = registry.evaluateAdapterReadiness("code-editor");
            const coverage = adapter.getIntentCoverage();
            const recovery = adapter.getRecoveryCoverage();
            const intents = adapter.listIntents();
            const objects = adapter.listObjects();
            process.stdout.write(JSON.stringify({
              adapterId: adapter.id,
              scope: adapter.semanticRuntimeScope,
              version: adapter.version,
              readiness,
              coverage,
              recovery,
              registered: registry.listAdapters(),
              intentStatuses: Object.fromEntries(
                intents.map((intent) => [intent.id, intent.semanticStatus])
              ),
              objectIds: objects.map((object) => object.id)
            }));
            """
        )
    )

    assert result["adapterId"] == "code-editor-domain-adapter"
    assert result["scope"] == "code-editor-source-safe-authoring-v1"
    assert result["version"] == "code-editor-semantic-adapter-v1"
    assert "code-editor" in {entry["appId"] for entry in result["registered"]}

    readiness = result["readiness"]
    assert readiness["semanticRuntimeReady"] is True
    assert readiness["runtimeCoreReady"] is True
    assert readiness["fullApplicationSemanticReady"] is True
    assert readiness["adapterExecutable"] is True
    assert readiness["actionPlannerReady"] is True
    assert readiness["capabilityProviderReady"] is True
    assert readiness["stateMachineReady"] is True
    assert readiness["recoveryReady"] is True
    assert readiness["executableIntentCount"] == 6
    assert readiness["prohibitedIntentCount"] == 1
    assert readiness["declaredOnlyIntentCount"] == 0
    assert readiness["preflightOnlyIntentCount"] == 0
    assert readiness["intentCoverageValidation"]["passed"] is True

    assert result["coverage"]["fullApplicationSemanticReady"] is True
    assert len(result["coverage"]["entries"]) == 7
    assert result["coverage"]["verification"]["passed"] is True
    assert result["recovery"]["coverageReady"] is True
    assert result["intentStatuses"] == {
        "inspectWorkspace": "executable",
        "openFile": "executable",
        "editDraft": "executable",
        "saveFile": "executable",
        "previewAiderPlan": "executable",
        "applyReviewedPatch": "executable",
        "runCode": "prohibited",
    }
    assert set(result["objectIds"]) == {
        "source-workspace",
        "file-tree",
        "active-file",
        "dirty-draft",
        "aider-context",
        "scm-evidence",
        "execution-policy",
    }


def test_code_editor_adapter_blocks_hidden_mutations_and_unreviewed_application() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            adapter.resetState({
              workspace: {
                id: "workspace-main",
                title: "Main Computer",
                root: "C:/work/main_computer",
                files: [{ path: "src/app.js", kind: "file", owned: true }]
              },
              activeFile: { path: "src/app.js", content: "console.log(1);", language: "javascript" }
            });
            const preview = adapter.preflightIntent("previewAiderPlan", {
              instruction: "Plan the smallest refactor.",
              selectedFiles: ["src/app.js"],
              gitPush: true
            });
            const save = adapter.preflightIntent("saveFile", {
              path: "src/app.js",
              text: "console.log(2);",
              explicitSave: true,
              writePolicy: "author-owned-source"
            });
            const apply = adapter.preflightIntent("applyReviewedPatch", {
              reviewedPatch: { id: "patch-1", files: ["src/app.js"] },
              approved: true,
              recoveryPath: "restore snapshot"
            });
            const run = adapter.preflightIntent("runCode", { command: "npm test" });
            process.stdout.write(JSON.stringify({ preview, save, apply, run }));
            """
        )
    )

    assert result["preview"]["status"] == "blocked"
    assert result["preview"]["blockers"][0]["code"] == "hidden-mutation-prohibited"
    assert result["save"]["status"] == "blocked"
    assert {blocker["code"] for blocker in result["save"]["blockers"]} == {
        "runtime-binding-unavailable",
        "stale-source-check-required",
    }
    assert result["apply"]["status"] == "blocked"
    assert {blocker["code"] for blocker in result["apply"]["blockers"]} == {
        "patch-approval-required",
        "runtime-binding-unavailable",
        "stale-source-check-required",
    }
    assert result["run"]["status"] == "blocked"
    assert result["run"]["blockers"][0]["code"] == "command-execution-prohibited"


def test_code_editor_adapter_executes_source_safe_workflow_with_receipts() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            (async () => {
              const calls = [];
              adapter.resetState();
              adapter.setRuntimeBindings({
                inspectWorkspace(payload) {
                  calls.push(["inspectWorkspace", payload]);
                  return {
                    ok: true,
                    workspace: {
                      id: "workspace-main",
                      title: "Main Computer",
                      root: "C:/work/main_computer",
                      files: [{ path: "src/app.js", kind: "file", owned: true }]
                    },
                    activeFile: {
                      path: "src/app.js",
                      content: "console.log(1);",
                      language: "javascript"
                    }
                  };
                },
                openFile(payload) {
                  calls.push(["openFile", payload]);
                  return {
                    ok: true,
                    activeFile: {
                      path: payload.path,
                      content: "console.log(1);",
                      language: "javascript"
                    }
                  };
                },
                saveFile(payload) {
                  calls.push(["saveFile", payload]);
                  return { ok: true, savedPath: payload.path };
                },
                applyReviewedPatch(payload) {
                  calls.push(["applyReviewedPatch", payload]);
                  return { ok: true, changedFiles: payload.reviewedPatch.files };
                }
              });

              const inspect = await adapter.executeIntent("inspectWorkspace");
              const open = await adapter.executeIntent("openFile", { path: "src/app.js" });
              const edit = await adapter.executeIntent("editDraft", {
                path: "src/app.js",
                text: "console.log(2);"
              });
              const save = await adapter.executeIntent("saveFile", {
                path: "src/app.js",
                text: "console.log(2);",
                explicitSave: true,
                staleSourceChecked: true,
                writePolicy: "author-owned-source"
              });
              const preview = await adapter.executeIntent("previewAiderPlan", {
                instruction: "Extract a helper.",
                selectedFiles: ["src/app.js"]
              });
              const apply = await adapter.executeIntent("applyReviewedPatch", {
                reviewedPatch: { id: "patch-1", files: ["src/app.js"] },
                reviewed: true,
                approved: true,
                staleSourceChecked: true,
                recoveryPath: "restore snapshot"
              });
              const receipts = adapter.listReceipts();
              process.stdout.write(JSON.stringify({
                inspect, open, edit, save, preview, apply,
                calls,
                receipts,
                state: adapter.getState()
              }));
            })().catch((error) => {
              process.stderr.write(error && error.stack ? error.stack : String(error));
              process.exit(1);
            });
            """
        )
    )

    assert result["inspect"]["status"] == "pass"
    assert result["open"]["status"] == "pass"
    assert result["edit"]["status"] == "pass"
    assert result["save"]["status"] == "pass"
    assert result["save"]["receipt"]["mutationAllowed"] is True
    assert result["preview"]["status"] == "pass"
    assert result["preview"]["receipt"]["mutationAllowed"] is False
    assert result["apply"]["status"] == "pass"
    assert result["apply"]["receipt"]["mutationAllowed"] is True
    assert [call[0] for call in result["calls"]] == [
        "inspectWorkspace",
        "openFile",
        "saveFile",
        "applyReviewedPatch",
    ]
    assert len(result["receipts"]) == 6
    assert all(receipt["schema"] == "mcel-semantic-receipt-v1" for receipt in result["receipts"])
    assert result["state"]["activeFile"]["path"] == "src/app.js"
    assert result["state"]["dirtyDraft"]["dirty"] is False
    assert result["state"]["aiderPlan"]["status"] == "previewed"
    assert result["state"]["patchApplication"]["status"] == "applied"


def test_code_editor_requirements_and_acceptance_binding_are_enforceable() -> None:
    result = run_node_json(
        textwrap.dedent(
            f"""
            const requirements = require({json.dumps(str(REQUIREMENTS))});
            const contract = requirements.getAppContract("code-editor");
            process.stdout.write(JSON.stringify({{
              currentRuntimeStatus: contract.current_runtime_status,
              appSummary: requirements.getSummary().app_counts["code-editor"]
            }}));
            """
        )
    )

    bindings = json.loads((ROOT / "main_computer" / "mcel_acceptance_bindings.json").read_text(encoding="utf-8"))
    code_editor_bindings = [
        binding for binding in bindings["bindings"]
        if binding["acceptanceContractId"] == "code-editor.acceptance.full-semantic-runtime"
    ]
    requirements_doc = (ROOT / "pretty_docs" / "mcel-code-editor-requirements.md").read_text(encoding="utf-8")

    assert result["currentRuntimeStatus"] == "fullApplicationSemanticReady"
    assert result["appSummary"] == 44
    assert len(code_editor_bindings) == 1
    assert code_editor_bindings[0]["selectors"] == ["tests/test_mcel_code_editor_semantic_adapter.py"]
    assert "id: code-editor.acceptance.full-semantic-runtime" in requirements_doc
    assert "status: specified" in requirements_doc
    assert "Code Editor has a registered domain adapter." in requirements_doc
