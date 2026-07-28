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
ADAPTER = SCRIPTS / "file-explorer-semantic-adapter.js"
FILE_EXPLORER = SCRIPTS / "file-explorer.js"
SHELL = ROOT / "main_computer" / "web" / "applications.html"


def run_node_json(script: str) -> dict:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; File Explorer semantic-adapter tests cannot run")
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
        const adapter = require({json.dumps(str(ADAPTER))});
        {body}
        """
    )


def test_file_explorer_adapter_is_loaded_before_truth_consumers_and_ui_uses_it() -> None:
    shell = SHELL.read_text(encoding="utf-8")
    registry_include = "<!-- @include applications/scripts/mcel-domain-adapter-registry.js -->"
    toolkit_include = "<!-- @include applications/scripts/mcel-semantic-adapter-toolkit.js -->"
    adapter_include = "<!-- @include applications/scripts/file-explorer-semantic-adapter.js -->"
    truth_include = "<!-- @include applications/scripts/mcel-app-truth-gate.js -->"
    planner_include = "<!-- @include applications/scripts/mcel-specimen-planner.js -->"

    for include in (registry_include, toolkit_include, adapter_include, truth_include, planner_include):
        assert include in shell

    assert shell.index(registry_include) < shell.index(toolkit_include)
    assert shell.index(toolkit_include) < shell.index(adapter_include)
    assert shell.index(adapter_include) < shell.index(planner_include)

    source = FILE_EXPLORER.read_text(encoding="utf-8")
    assert "window.FileExplorerSemanticAdapter" in source
    assert 'semanticAdapter.requestEndpoint(path, payload)' in source


def test_file_explorer_adapter_proves_full_current_read_only_scope() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            const readiness = registry.evaluateAdapterReadiness("file-explorer");
            const coverage = adapter.getIntentCoverage();
            const recovery = adapter.getRecoveryCoverage();
            const intents = adapter.listIntents();
            process.stdout.write(JSON.stringify({
              readiness,
              coverage,
              recovery,
              registered: registry.listAdapters(),
              intentStatuses: Object.fromEntries(
                intents.map((intent) => [intent.id, intent.semanticStatus])
              )
            }));
            """
        )
    )

    readiness = result["readiness"]
    assert readiness["adapterId"] == "file-explorer-domain-adapter"
    assert readiness["adapterVersion"] == "file-explorer-semantic-adapter-read-only-v1"
    assert readiness["runtimeCoreReady"] is True
    assert readiness["fullApplicationSemanticReady"] is True
    assert readiness["semanticRuntimeReady"] is True
    assert readiness["semanticRuntimeScope"] == "bounded-read-only-file-explorer-v1"
    assert readiness["adapterExecutable"] is True
    assert readiness["stateMachineReady"] is True
    assert readiness["actionPlannerReady"] is True
    assert readiness["capabilityProviderReady"] is True
    assert readiness["recoveryReady"] is True
    assert readiness["recoveryCoverageReady"] is True
    assert readiness["intentCoverageAuditReady"] is True
    assert readiness["intentCoverageReady"] is True
    assert readiness["executableIntentCount"] == 7
    assert readiness["prohibitedIntentCount"] == 3
    assert readiness["preflightOnlyIntentCount"] == 0
    assert readiness["declaredOnlyIntentCount"] == 0
    assert readiness["totalIntentCount"] == 10
    assert readiness["missingSemantics"] == []
    assert readiness["missingApplicationSemantics"] == []

    assert result["registered"] == [
        {
            "id": "file-explorer-domain-adapter",
            "appId": "file-explorer",
            "version": "file-explorer-semantic-adapter-read-only-v1",
            "kind": "bounded-read-only-file-navigation-domain-adapter",
        }
    ]

    coverage = result["coverage"]
    assert coverage["fullApplicationSemanticReady"] is True
    assert coverage["verification"]["passed"] is True
    assert coverage["excludedPlannedIntentIds"] == ["openInOwningApp"]
    assert set(coverage["prohibitedIntentIds"]) == {
        "deleteFile",
        "moveOrRename",
        "runFileCommand",
    }
    assert all(
        entry["mutates"] is False
        for entry in coverage["entries"]
        if entry["status"] == "executable"
    )

    assert result["recovery"]["coverageReady"] is True
    assert result["recovery"]["unverifiedFailureClasses"] == []
    assert result["intentStatuses"]["openInOwningApp"] == "preflight-only"


def test_file_explorer_adapter_executes_read_only_workflow_and_emits_receipts() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            const calls = [];
            adapter.resetState();
            adapter.setTransport(async (path, payload) => {
              calls.push({path, payload});
              if (path.endsWith("/roots")) {
                return {
                  ok: true,
                  roots: [
                    {id: "workspace", label: "Workspace", path_display: "C:/work", read_only: true}
                  ],
                  count: 1,
                  read_only: true
                };
              }
              if (path.endsWith("/list")) {
                return {
                  ok: true,
                  root_id: payload.root_id,
                  relative_path: payload.relative_path || "",
                  entries: [
                    {
                      kind: "file",
                      name: "README.md",
                      relative_path: "README.md",
                      path_display: "workspace:/README.md",
                      extension: ".md",
                      bytes: 12,
                      category: "text",
                      suggested_app: "document"
                    }
                  ],
                  count: 1,
                  read_only: true
                };
              }
              if (path.endsWith("/search")) {
                return {
                  ok: true,
                  root_id: payload.root_id,
                  query: payload.query,
                  results: [
                    {kind: "file", name: "README.md", relative_path: "README.md"}
                  ],
                  count: 1,
                  read_only: true
                };
              }
              if (path.endsWith("/read")) {
                return {
                  ok: true,
                  readable: true,
                  entry: {
                    kind: "file",
                    name: "README.md",
                    relative_path: "README.md",
                    category: "text",
                    suggested_app: "document"
                  },
                  content: "# Hello",
                  encoding: "utf-8",
                  read_only: true
                };
              }
              throw new Error("unexpected path " + path);
            });

            (async () => {
              const roots = await adapter.executeIntent("inspectRoots");
              const selected = await adapter.executeIntent("selectRoot", {
                rootId: "workspace",
                relativePath: ""
              });
              const search = await adapter.executeIntent("searchCurrentFolder", {
                query: "readme"
              });
              const preview = await adapter.executeIntent("previewEntry", {
                relativePath: "README.md"
              });
              const classification = await adapter.executeIntent("classifyEntry", {
                entry: {kind: "file", name: "tool.py", relative_path: "tool.py"}
              });
              const endpointResult = await adapter.requestEndpoint(
                "/api/applications/file-explorer/list",
                {root_id: "workspace", relative_path: ""}
              );
              process.stdout.write(JSON.stringify({
                calls,
                roots,
                selected,
                search,
                preview,
                classification,
                endpointResult,
                state: adapter.getState(),
                receipts: adapter.listReceipts(),
                evidence: adapter.mapEvidence(),
                objects: adapter.listObjects()
              }));
            })().catch((error) => {
              console.error(error && error.stack ? error.stack : error);
              process.exit(1);
            });
            """
        )
    )

    assert [call["path"] for call in result["calls"]] == [
        "/api/applications/file-explorer/roots",
        "/api/applications/file-explorer/list",
        "/api/applications/file-explorer/search",
        "/api/applications/file-explorer/read",
        "/api/applications/file-explorer/list",
    ]
    assert all(
        execution["status"] == "pass"
        for execution in (
            result["roots"],
            result["selected"],
            result["search"],
            result["preview"],
            result["classification"],
        )
    )
    assert result["classification"]["result"] == {
        "category": "code",
        "suggestedApp": "code-editor",
        "source": "adapter-extension-classifier",
    }
    assert result["endpointResult"]["read_only"] is True

    state = result["state"]
    assert state["phase"] == "ready"
    assert state["readOnly"] is True
    assert state["selectedRootId"] == "workspace"
    assert state["relativePath"] == ""
    assert state["entries"][0]["name"] == "README.md"

    receipts = result["receipts"]
    assert len(receipts) == 6
    assert all(receipt["readOnly"] is True for receipt in receipts)
    assert all(receipt["mutationAttempted"] is False for receipt in receipts)
    assert all(receipt["status"] == "pass" for receipt in receipts)
    assert any(item["receiptBacked"] is True for item in result["evidence"])
    assert {item["id"] for item in result["objects"]} >= {
        "file-explorer-state",
        "trusted-roots",
        "current-directory",
        "directory-entries",
    }


def test_file_explorer_adapter_blocks_traversal_unknown_roots_and_mutations_without_transport() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            let calls = 0;
            adapter.resetState();
            adapter.setTransport(async (path) => {
              calls += 1;
              if (path.endsWith("/roots")) {
                return {ok: true, roots: [{id: "workspace", label: "Workspace"}], count: 1, read_only: true};
              }
              throw new Error("transport should not be reached");
            });

            (async () => {
              await adapter.executeIntent("inspectRoots");
              const traversal = await adapter.executeIntent("listDirectory", {
                rootId: "workspace",
                relativePath: "../secret"
              });
              const unknownRoot = await adapter.executeIntent("listDirectory", {
                rootId: "not-trusted",
                relativePath: ""
              });
              const deletion = await adapter.executeIntent("deleteFile", {
                rootId: "workspace",
                relativePath: "README.md"
              });
              const command = await adapter.executeIntent("runFileCommand", {
                rootId: "workspace",
                relativePath: "README.md",
                command: "rm"
              });
              const handoff = await adapter.executeIntent("openInOwningApp", {
                rootId: "workspace",
                relativePath: "README.md",
                targetApp: "document"
              });
              process.stdout.write(JSON.stringify({
                calls,
                traversal,
                unknownRoot,
                deletion,
                command,
                handoff,
                receipts: adapter.listReceipts()
              }));
            })().catch((error) => {
              console.error(error && error.stack ? error.stack : error);
              process.exit(1);
            });
            """
        )
    )

    assert result["calls"] == 1
    assert result["traversal"]["status"] == "blocked"
    assert result["traversal"]["preflight"]["blockers"][0]["code"] == "path-invalid"
    assert result["unknownRoot"]["status"] == "blocked"
    assert "unknown-root" in {
        blocker["code"] for blocker in result["unknownRoot"]["preflight"]["blockers"]
    }
    assert result["deletion"]["status"] == "blocked"
    assert result["command"]["status"] == "blocked"
    assert result["handoff"]["status"] == "blocked"
    assert result["handoff"]["preflight"]["blockers"][0]["code"] == "handoff-not-implemented"
    assert all(receipt["mutationAttempted"] is False for receipt in result["receipts"])


def test_file_explorer_adapter_classifies_transport_failure_and_builds_safe_recovery() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            adapter.resetState();
            adapter.setTransport(async (path) => {
              if (path.endsWith("/roots")) {
                return {ok: true, roots: [{id: "workspace", label: "Workspace"}], count: 1};
              }
              const error = new Error("directory request failed");
              error.code = "request-failed";
              throw error;
            });

            (async () => {
              await adapter.executeIntent("inspectRoots");
              const failed = await adapter.executeIntent("listDirectory", {
                rootId: "workspace",
                relativePath: ""
              });
              process.stdout.write(JSON.stringify({
                failed,
                state: adapter.getState(),
                recoveryCoverage: adapter.getRecoveryCoverage()
              }));
            })().catch((error) => {
              console.error(error && error.stack ? error.stack : error);
              process.exit(1);
            });
            """
        )
    )

    failed = result["failed"]
    assert failed["status"] == "fail"
    assert failed["failure"]["failureClass"] == "request-failed"
    assert failed["failure"]["mutationAllowed"] is False
    assert failed["recovery"]["mutationAllowed"] is False
    assert {option["intentId"] for option in failed["recovery"]["options"]} == {
        "inspectRoots",
        "listDirectory",
    }
    assert result["state"]["phase"] == "error"
    assert result["state"]["error"]["code"] == "request-failed"
    assert result["recoveryCoverage"]["coverageReady"] is True


def test_actual_truth_gate_can_prove_file_explorer_semantic_runtime_with_bound_evidence() -> None:
    requirements = SCRIPTS / "mcel-requirements-registry.js"
    surface = SCRIPTS / "mcel-app-surface-registry.js"
    truth_gate = SCRIPTS / "mcel-app-truth-gate.js"
    result = run_node_json(
        textwrap.dedent(
            f"""
            const fs = require("fs");
            const vm = require("vm");
            const requirementsRegistry = require({json.dumps(str(requirements))});
            const domainAdapterRegistry = require({json.dumps(str(REGISTRY))});
            require({json.dumps(str(ADAPTER))});
            const surfaceSandbox = {{console}};
            surfaceSandbox.window = surfaceSandbox;
            vm.runInNewContext(
              fs.readFileSync({json.dumps(str(surface))}, "utf8"),
              surfaceSandbox,
              {{filename: "mcel-app-surface-registry.js"}}
            );
            const appSurfaceRegistry = surfaceSandbox.McelAppSurfaceRegistry;
            const gate = require({json.dumps(str(truth_gate))});
            const runtimeEvidence = {{
              schema: "mcel-runtime-flog-report-v2",
              generatedAt: "2026-07-27T22:00:00Z",
              results: [{{
                app: "file-explorer",
                status: "pass",
                timestamp: "2026-07-27T22:00:00Z",
                appSurfacePolicyScope: {{
                  status: "pass",
                  failedLayerIds: [],
                  unavailableLayerIds: [],
                  requiredLayerStatuses: {{
                    "semantic-surface": "pass",
                    "layout-grammar": "pass",
                    "runtime-ownership": "pass",
                    "runtime-visual-fit": "pass",
                    "diagnostic-no-throw": "pass"
                  }}
                }},
                appSurfaceConformance: {{
                  appId: "file-explorer",
                  status: "pass",
                  policyFailedLayerIds: [],
                  policyUnavailableLayerIds: [],
                  layers: [
                    {{id: "semantic-surface", status: "pass"}},
                    {{id: "layout-grammar", status: "pass"}},
                    {{id: "runtime-ownership", status: "pass"}},
                    {{id: "runtime-visual-fit", status: "pass"}},
                    {{id: "diagnostic-no-throw", status: "pass"}}
                  ]
                }}
              }}]
            }};
            const acceptanceEvidence = {{
              "file-explorer": {{
                appId: "file-explorer",
                status: "pass",
                testCount: 3,
                timestamp: "2026-07-27T22:00:00Z"
              }}
            }};
            const truth = gate.evaluateAppTruth("file-explorer", {{
              requirementsRegistry,
              domainAdapterRegistry,
              appSurfaceRegistry,
              runtimeEvidence,
              acceptanceEvidence,
              now: "2026-07-27T23:00:00Z"
            }});
            process.stdout.write(JSON.stringify(truth));
            """
        )
    )

    assert result["requirements"]["contractComplete"] is True
    assert result["adapter"]["registered"] is True
    assert result["adapter"]["runtimeCoreReady"] is True
    assert result["adapter"]["fullApplicationSemanticReady"] is True
    assert result["evidence"]["runtime"]["policyPassed"] is True
    assert result["claims"]["runtimeSurfaceProven"] is True
    assert result["claims"]["acceptanceProven"] is True
    assert result["claims"]["semanticRuntimeProven"] is True
    assert result["overallStatus"] == "semantic-runtime-proven"
    assert result["adapter"]["excludedPlannedIntentIds"] == ["openInOwningApp"]
    assert "missing-domain-adapter" not in result["findingCodes"]
    assert "required-intent-not-executable" not in result["findingCodes"]
