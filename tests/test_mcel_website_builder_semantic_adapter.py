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
ADAPTER = SCRIPTS / "website-builder-semantic-adapter.js"
REQUIREMENTS = SCRIPTS / "mcel-requirements-registry.js"
SHELL = ROOT / "main_computer" / "web" / "applications.html"


def run_node_json(script: str) -> dict:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; Website Builder semantic-adapter tests cannot run")
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


def test_website_builder_adapter_loads_after_toolkit_and_before_truth_consumers() -> None:
    shell = SHELL.read_text(encoding="utf-8")
    registry_include = "<!-- @include applications/scripts/mcel-domain-adapter-registry.js -->"
    toolkit_include = "<!-- @include applications/scripts/mcel-semantic-adapter-toolkit.js -->"
    adapter_include = "<!-- @include applications/scripts/website-builder-semantic-adapter.js -->"
    truth_include = "<!-- @include applications/scripts/mcel-app-truth-gate.js -->"
    planner_include = "<!-- @include applications/scripts/mcel-specimen-planner.js -->"

    for include in (registry_include, toolkit_include, adapter_include, truth_include, planner_include):
        assert include in shell

    assert shell.index(registry_include) < shell.index(toolkit_include)
    assert shell.index(toolkit_include) < shell.index(adapter_include)
    assert adapter_include in shell
    assert shell.index(adapter_include) < shell.index(truth_include)
    assert shell.index(adapter_include) < shell.index(planner_include)


def test_website_builder_adapter_proves_bounded_semantic_runtime_scope() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            const readiness = registry.evaluateAdapterReadiness("website-builder");
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

    assert result["adapterId"] == "website-builder-domain-adapter"
    assert result["scope"] == "website-builder-site-authoring-publish-handoff-v1"
    assert result["version"] == "website-builder-semantic-adapter-v1"
    assert "website-builder" in {entry["appId"] for entry in result["registered"]}

    readiness = result["readiness"]
    assert readiness["semanticRuntimeReady"] is True
    assert readiness["runtimeCoreReady"] is True
    assert readiness["fullApplicationSemanticReady"] is True
    assert readiness["adapterExecutable"] is True
    assert readiness["actionPlannerReady"] is True
    assert readiness["capabilityProviderReady"] is True
    assert readiness["stateMachineReady"] is True
    assert readiness["recoveryReady"] is True
    assert readiness["executableIntentCount"] == 12
    assert readiness["prohibitedIntentCount"] == 0
    assert readiness["declaredOnlyIntentCount"] == 0
    assert readiness["preflightOnlyIntentCount"] == 0
    assert readiness["intentCoverageValidation"]["passed"] is True

    assert result["coverage"]["fullApplicationSemanticReady"] is True
    assert len(result["coverage"]["entries"]) == 12
    assert result["coverage"]["verification"]["passed"] is True
    assert result["coverage"]["verification"]["gitCommitPushDelegatedToGitTools"] is True
    assert result["recovery"]["coverageReady"] is True
    assert result["intentStatuses"] == {
        "listSites": "executable",
        "selectSite": "executable",
        "editDraft": "executable",
        "saveSite": "executable",
        "previewDraft": "executable",
        "configureBlogRuntime": "executable",
        "publishLocalServer": "executable",
        "publishDev": "executable",
        "publishRemoteProduction": "executable",
        "openVisitUrl": "executable",
        "prepareGitToolsHandoff": "executable",
        "applyGeneratedWebsiteEdit": "executable",
    }
    assert set(result["objectIds"]) == {
        "site-catalog",
        "selected-website-project",
        "authoring-draft",
        "preview-evidence",
        "blog-runtime-contract",
        "publish-lane-state",
        "git-tools-handoff",
        "generated-edit-review",
    }


def test_website_builder_adapter_blocks_hidden_git_and_unconfirmed_mutations() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            adapter.resetState({
              sites: [{id: "hub-site", title: "Hub Site"}],
              selectedSiteId: "hub-site"
            });
            const save = adapter.preflightIntent("saveSite", {
              siteId: "hub-site",
              html: "<main>Updated</main>",
              intendedArtifacts: ["index.html"],
              explicitSave: true
            });
            const remote = adapter.preflightIntent("publishRemoteProduction", {
              siteId: "hub-site",
              savedSource: true,
              targetUrl: "https://example.test",
              confirmed: true
            });
            const handoff = adapter.preflightIntent("prepareGitToolsHandoff", {
              siteId: "hub-site",
              changedFiles: ["runtime/websites/hub-site/index.html"],
              gitPush: true
            });
            const edit = adapter.preflightIntent("applyGeneratedWebsiteEdit", {
              siteId: "hub-site",
              proposal: {id: "rag-1"},
              replacementFiles: ["runtime/websites/hub-site/index.html"],
              reviewed: true,
              approved: true,
              validationPassed: true,
              staleSourceChecked: true
            });
            process.stdout.write(JSON.stringify({save, remote, handoff, edit}));
            """
        )
    )

    assert result["save"]["status"] == "blocked"
    assert {blocker["code"] for blocker in result["save"]["blockers"]} == {
        "runtime-binding-unavailable",
        "stale-source-check-required",
    }
    assert result["remote"]["status"] == "blocked"
    assert {blocker["code"] for blocker in result["remote"]["blockers"]} == {
        "runtime-binding-unavailable",
        "remote-target-acceptance-required",
    }
    assert result["handoff"]["status"] == "blocked"
    assert result["handoff"]["blockers"][0]["code"] == "hidden-git-mutation-prohibited"
    assert result["edit"]["status"] == "blocked"
    assert {blocker["code"] for blocker in result["edit"]["blockers"]} == {
        "runtime-binding-unavailable",
        "recovery-path-required",
    }


def test_website_builder_adapter_executes_author_preview_publish_handoff_workflow() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            (async () => {
              const calls = [];
              adapter.resetState();
              adapter.setRuntimeBindings({
                saveSite(payload) {
                  calls.push(["saveSite", payload.siteId]);
                  return {
                    ok: true,
                    artifacts: payload.intendedArtifacts,
                    savedPath: "runtime/websites/" + payload.siteId
                  };
                },
                configureBlogRuntime(payload) {
                  calls.push(["configureBlogRuntime", payload.siteId]);
                  return {
                    ok: true,
                    layers: ["database", "cms", "blog"],
                    directusConnection: payload.directusConnection
                  };
                },
                publishLocalServer(payload) {
                  calls.push(["publishLocalServer", payload.siteId]);
                  return {ok: true, url: "http://127.0.0.1:8765/sites/hub-site/"};
                },
                publishDev(payload) {
                  calls.push(["publishDev", payload.siteId]);
                  return {ok: true, url: "https://dev.example.test"};
                },
                publishRemoteProduction(payload) {
                  calls.push(["publishRemoteProduction", payload.siteId]);
                  return {ok: true, url: "https://www.example.test"};
                },
                applyGeneratedWebsiteEdit(payload) {
                  calls.push(["applyGeneratedWebsiteEdit", payload.siteId]);
                  return {ok: true, changedFiles: payload.replacementFiles};
                }
              });

              const list = await adapter.executeIntent("listSites");
              const select = await adapter.executeIntent("selectSite", {siteId: "hub-site"});
              const edit = await adapter.executeIntent("editDraft", {
                siteId: "hub-site",
                html: "<main>Updated</main>",
                fields: ["html"]
              });
              const save = await adapter.executeIntent("saveSite", {
                siteId: "hub-site",
                html: "<main>Updated</main>",
                intendedArtifacts: ["index.html", "style.css"],
                explicitSave: true,
                staleSourceChecked: true
              });
              const preview = await adapter.executeIntent("previewDraft", {
                siteId: "hub-site",
                html: "<main>Updated</main>"
              });
              const runtime = await adapter.executeIntent("configureBlogRuntime", {
                siteId: "hub-site",
                confirmed: true,
                directusConnection: {mode: "use_existing", database_volume: "hub_site_directus"},
                storageAcknowledged: true
              });
              const local = await adapter.executeIntent("publishLocalServer", {
                siteId: "hub-site",
                savedSource: true,
                targetUrl: "http://127.0.0.1:8765/sites/hub-site/",
                confirmed: true
              });
              const dev = await adapter.executeIntent("publishDev", {
                siteId: "hub-site",
                savedSource: true,
                targetUrl: "https://dev.example.test",
                confirmed: true
              });
              const remote = await adapter.executeIntent("publishRemoteProduction", {
                siteId: "hub-site",
                savedSource: true,
                targetUrl: "https://www.example.test",
                confirmed: true,
                acceptedRemoteTarget: true
              });
              const visit = await adapter.executeIntent("openVisitUrl", {
                siteId: "hub-site",
                lane: "remote_prod",
                visitUrl: "https://www.example.test"
              });
              const handoff = await adapter.executeIntent("prepareGitToolsHandoff", {
                siteId: "hub-site",
                changedFiles: ["runtime/websites/hub-site/index.html"]
              });
              const generated = await adapter.executeIntent("applyGeneratedWebsiteEdit", {
                siteId: "hub-site",
                proposal: {id: "rag-1"},
                replacementFiles: ["runtime/websites/hub-site/index.html"],
                reviewed: true,
                approved: true,
                validationPassed: true,
                staleSourceChecked: true,
                recoveryPath: "restore snapshot"
              });
              const receipts = adapter.listReceipts();
              process.stdout.write(JSON.stringify({
                list, select, edit, save, preview, runtime, local, dev, remote, visit, handoff, generated,
                calls,
                receipts,
                evidence: adapter.mapEvidence(),
                state: adapter.getState()
              }));
            })().catch((error) => {
              process.stderr.write(error && error.stack ? error.stack : String(error));
              process.exit(1);
            });
            """
        )
    )

    for key in (
        "list",
        "select",
        "edit",
        "save",
        "preview",
        "runtime",
        "local",
        "dev",
        "remote",
        "visit",
        "handoff",
        "generated",
    ):
        assert result[key]["status"] == "pass", key

    assert result["save"]["receipt"]["mutationAllowed"] is True
    assert result["preview"]["receipt"]["mutationAllowed"] is False
    assert result["remote"]["receipt"]["lane"] == "publish_remote_production"
    assert result["handoff"]["state"]["gitHandoff"]["owningAdapter"] == "git-tools"
    assert result["generated"]["receipt"]["mutationAllowed"] is True
    assert [call[0] for call in result["calls"]] == [
        "saveSite",
        "configureBlogRuntime",
        "publishLocalServer",
        "publishDev",
        "publishRemoteProduction",
        "applyGeneratedWebsiteEdit",
    ]
    assert len(result["receipts"]) == 12
    assert all(receipt["schema"] == "mcel-semantic-receipt-v1" for receipt in result["receipts"])
    assert result["state"]["selectedSiteId"] == "hub-site"
    assert result["state"]["draft"]["dirty"] is False
    assert result["state"]["runtimeSetup"]["status"] == "configured"
    assert result["state"]["publishReceipts"]["local"]["status"] == "published"
    assert result["state"]["publishReceipts"]["dev"]["url"] == "https://dev.example.test"
    assert result["state"]["publishReceipts"]["remote_prod"]["url"] == "https://www.example.test"
    assert result["evidence"]["boundaries"]["gitCommitPush"] == "delegated-to-git-tools"


def test_website_builder_requirements_and_acceptance_binding_are_enforceable() -> None:
    result = run_node_json(
        textwrap.dedent(
            f"""
            const requirements = require({json.dumps(str(REQUIREMENTS))});
            const contract = requirements.getAppContract("website-builder");
            process.stdout.write(JSON.stringify({{
              currentRuntimeStatus: contract.current_runtime_status,
              appSummary: requirements.getSummary().app_counts["website-builder"],
              acceptanceCount: contract.block_type_counts["mcel-acceptance"],
              adapterStatusCounts: contract.adapter_status_counts
            }}));
            """
        )
    )

    bindings = json.loads((ROOT / "main_computer" / "mcel_acceptance_bindings.json").read_text(encoding="utf-8"))
    website_builder_bindings = [
        binding for binding in bindings["bindings"]
        if binding["acceptanceContractId"] == "website-builder.acceptance.semantic-runtime"
    ]
    requirements_doc = (ROOT / "pretty_docs" / "mcel-website-builder-requirements.md").read_text(encoding="utf-8")

    assert result["currentRuntimeStatus"] == "fullApplicationSemanticReady"
    assert result["appSummary"] == 54
    assert result["acceptanceCount"] == 5
    assert result["adapterStatusCounts"] == {
        "current_adapter_status:executable": 12,
        "target_adapter_status:executable": 12,
    }
    assert len(website_builder_bindings) == 1
    assert website_builder_bindings[0]["selectors"] == ["tests/test_mcel_website_builder_semantic_adapter.py"]
    assert "current_semantic_runtime_scope: website-builder-site-authoring-publish-handoff-v1" in requirements_doc
    assert "id: website-builder.acceptance.semantic-runtime" in requirements_doc
    assert "status: specified" in requirements_doc
    assert "Website Builder domain adapter derives selected site state" in requirements_doc
