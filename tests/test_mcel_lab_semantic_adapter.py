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
TOOLKIT = SCRIPTS / "mcel-semantic-adapter-toolkit.js"
ADAPTER = SCRIPTS / "mcel-lab-semantic-adapter.js"
REQUIREMENTS = SCRIPTS / "mcel-requirements-registry.js"
SELF_DIAGNOSIS = SCRIPTS / "mcel-self-diagnosis.js"
SHELL = ROOT / "main_computer" / "web" / "applications.html"


def run_node_json(script: str) -> dict:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; MCEL Lab semantic-adapter tests cannot run")
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


def test_mcel_lab_adapter_loads_after_toolkit_and_before_truth_consumers() -> None:
    shell = SHELL.read_text(encoding="utf-8")
    registry_include = "<!-- @include applications/scripts/mcel-domain-adapter-registry.js -->"
    toolkit_include = "<!-- @include applications/scripts/mcel-semantic-adapter-toolkit.js -->"
    adapter_include = "<!-- @include applications/scripts/mcel-lab-semantic-adapter.js -->"
    truth_include = "<!-- @include applications/scripts/mcel-app-truth-gate.js -->"
    planner_include = "<!-- @include applications/scripts/mcel-specimen-planner.js -->"

    for include in (registry_include, toolkit_include, adapter_include, truth_include, planner_include):
        assert include in shell

    assert shell.index(registry_include) < shell.index(toolkit_include)
    assert shell.index(toolkit_include) < shell.index(adapter_include)
    assert shell.index(adapter_include) < shell.index(truth_include)
    assert shell.index(adapter_include) < shell.index(planner_include)


def test_mcel_lab_adapter_proves_blueprint_repair_context_runtime_scope() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            const readiness = registry.evaluateAdapterReadiness("mcel-lab");
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

    assert result["adapterId"] == "mcel-lab-domain-adapter"
    assert result["scope"] == "mcel-lab-blueprint-inspection-repair-context-v1"
    assert result["version"] == "mcel-lab-semantic-adapter-v1"
    assert "mcel-lab" in {entry["appId"] for entry in result["registered"]}

    readiness = result["readiness"]
    assert readiness["semanticRuntimeReady"] is True
    assert readiness["runtimeCoreReady"] is True
    assert readiness["fullApplicationSemanticReady"] is True
    assert readiness["adapterExecutable"] is True
    assert readiness["actionPlannerReady"] is True
    assert readiness["capabilityProviderReady"] is True
    assert readiness["stateMachineReady"] is True
    assert readiness["recoveryReady"] is True
    assert readiness["executableIntentCount"] == 7
    assert readiness["prohibitedIntentCount"] == 1
    assert readiness["declaredOnlyIntentCount"] == 0
    assert readiness["preflightOnlyIntentCount"] == 0
    assert readiness["intentCoverageValidation"]["passed"] is True

    assert result["coverage"]["fullApplicationSemanticReady"] is True
    assert len(result["coverage"]["entries"]) == 8
    assert result["coverage"]["verification"]["passed"] is True
    assert result["coverage"]["verification"]["selfHostingMutationBlocked"] is True
    assert result["recovery"]["coverageReady"] is True
    assert result["intentStatuses"] == {
        "selectAppBlueprint": "executable",
        "inspectAspect": "executable",
        "mountAppPreview": "executable",
        "inspectRenderedElement": "executable",
        "annotateRefactorCandidate": "executable",
        "validateBlueprintContract": "executable",
        "exportRepairContext": "executable",
        "applySelfMutation": "prohibited",
    }
    assert set(result["objectIds"]) == {
        "app-blueprint",
        "blueprint-aspect",
        "mounted-preview",
        "rendered-element-evidence",
        "refactor-annotation",
        "validation-finding",
        "repair-context",
        "patch-application-boundary",
    }


def test_mcel_lab_adapter_blocks_self_mutation_and_unreviewed_repair_export() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            adapter.resetState({
              selectedAppId: "mcel-lab",
              selectedAspectId: "repair"
            });
            const selfMutation = adapter.preflightIntent("applySelfMutation", {
              command: "apply patch to live lab source"
            });
            const hiddenMutation = adapter.preflightIntent("exportRepairContext", {
              reviewedFindingIds: ["finding-1"],
              instruction: "also git push"
            });
            const unreviewedExport = adapter.preflightIntent("exportRepairContext", {});
            const inspectWithoutPreview = adapter.preflightIntent("inspectRenderedElement", {
              inspectMode: true,
              selector: "#mcel-blueprint-work-surface"
            });
            process.stdout.write(JSON.stringify({
              selfMutation,
              hiddenMutation,
              unreviewedExport,
              inspectWithoutPreview
            }));
            """
        )
    )

    assert result["selfMutation"]["status"] == "blocked"
    assert result["selfMutation"]["blockers"][0]["code"] == "self-mutation-prohibited"
    assert result["hiddenMutation"]["status"] == "blocked"
    assert result["hiddenMutation"]["blockers"][0]["code"] == "hidden-mutation-prohibited"
    assert result["unreviewedExport"]["status"] == "blocked"
    assert result["unreviewedExport"]["blockers"][0]["code"] == "reviewed-findings-required"
    assert result["inspectWithoutPreview"]["status"] == "blocked"
    assert result["inspectWithoutPreview"]["blockers"][0]["code"] == "contained-preview-required"


def test_mcel_lab_adapter_executes_bounded_inspection_and_repair_context_flow() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            (async () => {
              const calls = [];
              adapter.resetState();
              adapter.setRuntimeBindings({
                selectAppBlueprint(payload) {
                  calls.push(["selectAppBlueprint", payload]);
                  return { ok: true, appId: payload.appId, aspectId: payload.aspectId || "overview" };
                },
                mountAppPreview(payload) {
                  calls.push(["mountAppPreview", payload]);
                  return { ok: true, appId: payload.appId, route: "/applications/calculator", rootSelector: "#calculator-app" };
                },
                validateBlueprintContract(payload) {
                  calls.push(["validateBlueprintContract", payload]);
                  return {
                    ok: true,
                    appId: payload.appId,
                    aspectId: payload.aspectId,
                    findings: [{ id: "finding-1", reviewed: true, source: "requirements-registry" }]
                  };
                },
                exportRepairContext(payload) {
                  calls.push(["exportRepairContext", payload]);
                  return {
                    ok: true,
                    appId: payload.appId,
                    aspectId: payload.aspectId,
                    reviewedFindingIds: payload.reviewedFindingIds,
                    annotationIds: payload.annotationIds
                  };
                }
              });

              const select = await adapter.executeIntent("selectAppBlueprint", {
                appId: "calculator",
                aspectId: "overview"
              });
              const inspect = await adapter.executeIntent("inspectAspect", {
                appId: "calculator",
                aspectId: "actions"
              });
              const mount = await adapter.executeIntent("mountAppPreview", {
                appId: "calculator",
                route: "/applications/calculator"
              });
              const element = await adapter.executeIntent("inspectRenderedElement", {
                appId: "calculator",
                inspectMode: true,
                selector: "#calculator-display",
                visibleText: "0"
              });
              const annotation = await adapter.executeIntent("annotateRefactorCandidate", {
                appId: "calculator",
                annotationIntent: "keep",
                rationale: "Primary result display remains useful.",
                reviewed: true
              });
              const validate = await adapter.executeIntent("validateBlueprintContract", {
                appId: "calculator",
                aspectId: "actions"
              });
              const exported = await adapter.executeIntent("exportRepairContext", {
                appId: "calculator",
                aspectId: "actions",
                reviewedFindingIds: ["finding-1"],
                annotationIds: [annotation.state.annotations[0].id],
                approved: true
              });
              process.stdout.write(JSON.stringify({
                select, inspect, mount, element, annotation, validate, exported,
                calls,
                receipts: adapter.listReceipts(),
                state: adapter.getState(),
                evidence: adapter.mapEvidence("repair-context")
              }));
            })().catch((error) => {
              process.stderr.write(error && error.stack ? error.stack : String(error));
              process.exit(1);
            });
            """
        )
    )

    assert result["select"]["status"] == "pass"
    assert result["inspect"]["status"] == "pass"
    assert result["mount"]["status"] == "pass"
    assert result["element"]["status"] == "pass"
    assert result["annotation"]["status"] == "pass"
    assert result["validate"]["status"] == "pass"
    assert result["exported"]["status"] == "pass"
    assert [call[0] for call in result["calls"]] == [
        "selectAppBlueprint",
        "mountAppPreview",
        "validateBlueprintContract",
        "exportRepairContext",
    ]
    assert len(result["receipts"]) == 7
    assert all(receipt["schema"] == "mcel-semantic-receipt-v1" for receipt in result["receipts"])
    assert all(receipt["mutationAllowed"] is False for receipt in result["receipts"])
    assert result["state"]["selectedAppId"] == "calculator"
    assert result["state"]["selectedAspectId"] == "actions"
    assert result["state"]["mountedPreview"]["contained"] is True
    assert result["state"]["selectedElement"]["selector"] == "#calculator-display"
    assert result["state"]["annotations"][0]["draft"] is True
    assert result["state"]["repairContext"]["patchApplicationBoundary"] == "external-new-patch-workflow"
    assert result["evidence"]["evidence"]["patchApplicationBoundary"] == "external-new-patch-workflow"


def test_mcel_lab_requirements_and_acceptance_binding_are_enforceable() -> None:
    result = run_node_json(
        textwrap.dedent(
            f"""
            const requirements = require({json.dumps(str(REQUIREMENTS))});
            const contract = requirements.getAppContract("mcel-lab");
            process.stdout.write(JSON.stringify({{
              currentRuntimeStatus: contract.current_runtime_status,
              targetRuntimeStatus: contract.target_runtime_status,
              appSummary: requirements.getSummary().app_counts["mcel-lab"],
              acceptanceIds: contract.acceptance_contract_ids || []
            }}));
            """
        )
    )

    bindings = json.loads((ROOT / "main_computer" / "mcel_acceptance_bindings.json").read_text(encoding="utf-8"))
    lab_bindings = [
        binding for binding in bindings["bindings"]
        if binding["acceptanceContractId"] == "mcel-lab.acceptance.semantic-runtime"
    ]
    requirements_doc = (ROOT / "pretty_docs" / "mcel-lab-blueprint-studio.md").read_text(encoding="utf-8")

    assert result["currentRuntimeStatus"] == "scope-limited-semantic-runtime"
    assert result["targetRuntimeStatus"] == "scope-limited-semantic-runtime"
    assert result["appSummary"] == 38
    assert len(lab_bindings) == 1
    assert lab_bindings[0]["selectors"] == ["tests/test_mcel_lab_semantic_adapter.py"]
    assert "id: mcel-lab.acceptance.semantic-runtime" in requirements_doc
    assert "status: specified" in requirements_doc
    assert "MCEL Lab has a registered domain adapter." in requirements_doc


def test_mcel_lab_runtime_visual_fit_probe_ignores_mounted_preview_clone() -> None:
    source = SELF_DIAGNOSIS.read_text(encoding="utf-8")
    ignored_start = source.index("function isLayoutProbeIgnoredElement")
    ignored_end = source.index("function directVisibleChildren", ignored_start)
    ignored_block = source[ignored_start:ignored_end]

    assert 'appId === "mcel-lab"' in ignored_block
    assert '[data-mcel-preview-clone]' in ignored_block
    assert "route-specific FLOG" in ignored_block
    assert ignored_block.index('appId === "mcel-lab"') < ignored_block.index('appId === "document"')
