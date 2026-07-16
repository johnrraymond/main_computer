from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_APP = PROJECT_ROOT / "main_computer/web/applications"
SCRIPTS = WEB_APP / "scripts"


def run_node_json(script: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_git_tools_semantic_adapter_load_order_is_before_planner() -> None:
    shell = (PROJECT_ROOT / "main_computer/web/applications.html").read_text(encoding="utf-8")

    status_api = "<!-- @include applications/scripts/git-tools-status-api.js -->"
    registry = "<!-- @include applications/scripts/mcel-domain-adapter-registry.js -->"
    enrichment = "<!-- @include applications/scripts/git-tools-mcel.js -->"
    semantic_adapter = "<!-- @include applications/scripts/git-tools-semantic-adapter.js -->"
    planner = "<!-- @include applications/scripts/mcel-specimen-planner.js -->"

    for include in (status_api, registry, enrichment, semantic_adapter, planner):
        assert include in shell

    assert shell.index(status_api) < shell.index(semantic_adapter)
    assert shell.index(registry) < shell.index(semantic_adapter)
    assert shell.index(enrichment) < shell.index(semantic_adapter)
    assert shell.index(semantic_adapter) < shell.index(planner)


def test_git_tools_adapter_derives_state_objects_intents_and_preflight_capability() -> None:
    registry_path = SCRIPTS / "mcel-domain-adapter-registry.js"
    adapter_path = SCRIPTS / "git-tools-semantic-adapter.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const calls = [];
        const sandbox = {{console}};
        sandbox.window = sandbox;
        sandbox.GitToolsStatusApi = {{
          async fetchStatus(options) {{
            calls.push(options);
            return {{
              ok: true,
              repo_dir: options.repoDir,
              git_root: "C:/work/main_computer_test",
              is_git_repo: true,
              has_head: true,
              branch: "main",
              ahead: 2,
              behind: 1,
              dirty: true,
              changed_count: 3,
              untracked_count: 1,
              short_status: "## main...origin/main [ahead 2, behind 1]",
              recent_commits: ["abc123 Test commit"],
              remotes: [
                {{name: "origin", fetch: "https://example.invalid/repo.git", push: "https://example.invalid/repo.git"}}
              ],
              patching: {{
                ok: true,
                counts: {{incoming: 4, applied: 2, archive: 1, dry_runs: 3}}
              }},
              capabilities: {{git_available: true}}
            }};
          }}
        }};
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(adapter_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-adapter.js"}}
        );
        (async () => {{
          const registry = sandbox.McelDomainAdapterRegistry;
          const adapter = sandbox.GitToolsSemanticAdapter;
          const state = await adapter.refreshState({{repoDir: "C:/work/main_computer_test"}});
          const objects = adapter.listObjects(state);
          const intents = adapter.listIntents(state);
          const evidence = adapter.mapEvidence(state);
          const readiness = registry.evaluateAdapterReadiness("git-tools");
          const push = intents.find((intent) => intent.id === "pushCurrentBranch");
          const refresh = intents.find((intent) => intent.id === "refreshStatus");
          console.log(JSON.stringify({{
            calls,
            adapterId: adapter.id,
            adapterKind: adapter.kind,
            registeredAdapters: registry.listAdapters(),
            readiness,
            state,
            objectIds: objects.map((item) => item.id),
            intentIds: intents.map((item) => item.id),
            refresh,
            push,
            evidence,
            methodPresence: {{
              executeIntent: typeof adapter.executeIntent,
              preflightIntent: typeof adapter.preflightIntent,
              buildReceipt: typeof adapter.buildReceipt,
              classifyFailure: typeof adapter.classifyFailure,
              buildRecoveryOptions: typeof adapter.buildRecoveryOptions,
              getRecoveryCoverage: typeof adapter.getRecoveryCoverage,
              getIntentCoverage: typeof adapter.getIntentCoverage
            }}
          }}));
        }})().catch((error) => {{
          console.error(error && error.stack ? error.stack : error);
          process.exit(1);
        }});
        """
    )
    result = run_node_json(script)

    assert result["calls"] == [{"repoDir": "C:/work/main_computer_test"}]
    assert result["adapterId"] == "git-tools-domain-adapter"
    assert result["adapterKind"] == "governed-push-execution-recovery-domain-adapter"
    assert result["registeredAdapters"] == [
        {
            "id": "git-tools-domain-adapter",
            "appId": "git-tools",
            "version": "git-tools-semantic-adapter-governed-push-v7",
            "kind": "governed-push-execution-recovery-domain-adapter",
        }
    ]

    readiness = result["readiness"]
    assert readiness["semanticRuntimeReady"] is False
    assert readiness["adapterExecutable"] is True
    assert readiness["stateMachineReady"] is True
    assert readiness["actionPlannerReady"] is True
    assert readiness["capabilityProviderReady"] is True
    assert readiness["recoveryReady"] is True
    assert readiness["recoveryClassifierPresent"] is True
    assert readiness["recoveryCoverageReady"] is True
    assert readiness["runtimeCoreReady"] is True
    assert readiness["fullApplicationSemanticReady"] is False
    assert readiness["semanticRuntimeScope"] == "governed-publish-partial"
    assert readiness["executableIntentCount"] == 2
    assert readiness["preflightOnlyIntentCount"] == 1
    assert readiness["declaredOnlyIntentCount"] == 3
    assert readiness["prohibitedIntentCount"] == 1
    assert readiness["blockedIntentCount"] == 4
    assert readiness["totalIntentCount"] == 7
    assert readiness["intentCoverageAuditReady"] is True
    assert readiness["intentCoverageReady"] is False
    assert readiness["missingApplicationSemantics"] == [
        "inspectPatchInventory",
        "inspectRemotes",
        "inspectWorkingTree",
        "preparePush",
    ]
    assert readiness["missingSemantics"] == []

    state = result["state"]
    assert state["phase"] == "ready"
    assert state["repoDir"] == "C:/work/main_computer_test"
    assert state["gitRoot"] == "C:/work/main_computer_test"
    assert state["branch"] == "main"
    assert state["dirty"] is True
    assert state["ahead"] == 2
    assert state["behind"] == 1
    assert state["changedCount"] == 3
    assert state["untrackedCount"] == 1
    assert state["patching"]["counts"]["dryRuns"] == 3
    assert state["remotes"][0]["name"] == "origin"

    assert result["objectIds"] == [
        "repository",
        "branch",
        "working-tree",
        "remotes",
        "patch-inventory",
    ]
    assert "preparePush" in result["intentIds"]
    assert "runManualCommand" in result["intentIds"]
    assert result["refresh"]["available"] is True
    assert result["refresh"]["executable"] is True
    assert result["refresh"]["semanticStatus"] == "executable"
    assert result["refresh"]["executionBinding"] == "git-tools-status-api.fetchStatus"
    assert result["push"]["available"] is True
    assert result["push"]["risk"] == "publish-mutation"
    assert result["push"]["semanticStatus"] == "executable"
    assert result["push"]["executable"] is True
    assert result["push"]["executionBinding"] == "git-tools-server-panel.serverPushLocal"
    assert result["push"]["blockedReason"] == (
        "A successful preflight and explicit confirmation are required before execution."
    )
    assert all(item["receiptBacked"] is False for item in result["evidence"])
    assert result["methodPresence"] == {
        "executeIntent": "function",
        "preflightIntent": "function",
        "buildReceipt": "function",
        "classifyFailure": "function",
        "buildRecoveryOptions": "function",
        "getRecoveryCoverage": "function",
        "getIntentCoverage": "function",
    }


def test_git_tools_adapter_promotes_only_after_verified_recovery_coverage() -> None:
    registry_path = SCRIPTS / "mcel-domain-adapter-registry.js"
    adapter_path = SCRIPTS / "git-tools-semantic-adapter.js"
    planner_path = SCRIPTS / "mcel-specimen-planner.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(adapter_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-adapter.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(planner_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-specimen-planner.js"}}
        );
        const registry = sandbox.McelDomainAdapterRegistry;
        const planner = sandbox.McelSpecimenPlanner;
        const plan = planner.planFor("git-tools");
        const readiness = planner.semanticReadinessForPlan(plan);
        const snapshot = registry.snapshotSemanticRuntime("git-tools", plan);
        console.log(JSON.stringify({{
          readiness,
          snapshot: {{
            stateSnapshotAvailable: snapshot.stateSnapshotAvailable,
            intentListAvailable: snapshot.intentListAvailable,
            statePhase: snapshot.state.phase,
            firstIntent: snapshot.intents[0].id
          }}
        }}));
        """
    )
    result = run_node_json(script)

    readiness = result["readiness"]
    assert readiness["semanticRuntimeAuthority"] == "mcel-domain-adapter-registry"
    assert readiness["registryProofPresent"] is True
    assert readiness["registryProofAuthorized"] is True
    assert readiness["semanticRuntimeReady"] is False
    assert readiness["runtimeCoreReady"] is True
    assert readiness["fullApplicationSemanticReady"] is False
    assert readiness["semanticRuntimeScope"] == "governed-publish-partial"
    assert readiness["semanticRuntimeStatus"] == "scope-limited-semantic-runtime"
    assert readiness["adapterKind"] == "scope-limited-executable-semantic-workbench"
    assert readiness["adapterExecutable"] is True
    assert readiness["stateMachineReady"] is True
    assert readiness["actionPlannerReady"] is True
    assert readiness["capabilityProviderReady"] is True
    assert readiness["recoveryReady"] is True

    assert result["snapshot"] == {
        "stateSnapshotAvailable": True,
        "intentListAvailable": True,
        "statePhase": "uninitialized",
        "firstIntent": "refreshStatus",
    }


def test_git_tools_adapter_captures_status_api_failure_without_mutation_fallback() -> None:
    registry_path = SCRIPTS / "mcel-domain-adapter-registry.js"
    adapter_path = SCRIPTS / "git-tools-semantic-adapter.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(adapter_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-adapter.js"}}
        );
        (async () => {{
          const adapter = sandbox.GitToolsSemanticAdapter;
          const state = await adapter.refreshState({{
            repoDir: "C:/broken",
            api: {{
              async fetchStatus() {{
                throw new Error("status backend offline");
              }}
            }}
          }});
          const intents = adapter.listIntents(state);
          console.log(JSON.stringify({{
            state,
            availableIntents: intents.filter((intent) => intent.available).map((intent) => intent.id),
            readiness: sandbox.McelDomainAdapterRegistry.evaluateAdapterReadiness("git-tools")
          }}));
        }})().catch((error) => {{
          console.error(error && error.stack ? error.stack : error);
          process.exit(1);
        }});
        """
    )
    result = run_node_json(script)

    assert result["state"]["phase"] == "error"
    assert result["state"]["repoDir"] == "C:/broken"
    assert result["state"]["error"] == {
        "code": "git-status-request-failed",
        "message": "status backend offline",
    }
    assert result["availableIntents"] == ["refreshStatus"]
    assert result["readiness"]["semanticRuntimeReady"] is False


def test_git_tools_preflight_classifies_risky_intents_and_builds_blocked_receipts() -> None:
    registry_path = SCRIPTS / "mcel-domain-adapter-registry.js"
    adapter_path = SCRIPTS / "git-tools-semantic-adapter.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const storageData = new Map();
        const sandbox = {{console}};
        sandbox.window = sandbox;
        sandbox.localStorage = {{
          getItem(key) {{ return storageData.has(key) ? storageData.get(key) : null; }},
          setItem(key, value) {{ storageData.set(key, String(value)); }},
          removeItem(key) {{ storageData.delete(key); }}
        }};
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(adapter_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-adapter.js"}}
        );

        const adapter = sandbox.GitToolsSemanticAdapter;
        const observedAt = "2026-07-15T19:00:00.000Z";
        function state(overrides = {{}}) {{
          return adapter.normalizeStatus({{
            ok: true,
            repo_dir: "C:/work/main_computer_test",
            git_root: "C:/work/main_computer_test",
            is_git_repo: true,
            has_head: true,
            branch: "main",
            ahead: 2,
            behind: 0,
            dirty: true,
            changed_count: 2,
            untracked_count: 1,
            remotes: [
              {{name: "origin", fetch: "https://example.invalid/repo.git", push: "https://example.invalid/repo.git"}}
            ],
            ...overrides
          }}, {{observedAt}});
        }}

        adapter.clearReceipts();
        const missingRemote = adapter.preflightIntent(
          "pushCurrentBranch",
          state({{remotes: []}}),
          {{now: "2026-07-15T19:00:30.000Z"}}
        );
        const diverged = adapter.preflightIntent(
          "pushCurrentBranch",
          state({{ahead: 2, behind: 1}}),
          {{now: "2026-07-15T19:00:30.000Z"}}
        );
        const confirmation = adapter.preflightIntent(
          "pushCurrentBranch",
          state(),
          {{now: "2026-07-15T19:00:30.000Z"}}
        );
        const prepare = adapter.preflightIntent(
          "preparePush",
          state(),
          {{now: "2026-07-15T19:00:30.000Z"}}
        );
        const manual = adapter.preflightIntent(
          "runManualCommand",
          state(),
          {{now: "2026-07-15T19:00:30.000Z"}}
        );
        const stale = adapter.preflightIntent(
          "pushCurrentBranch",
          state(),
          {{now: "2026-07-15T19:03:01.000Z", maxStateAgeMs: 120000}}
        );

        const receipts = adapter.listReceipts();
        const receiptEvidence = adapter
          .mapEvidence(state())
          .filter((entry) => entry.kind === "preflight-decision-receipt");
        const readiness = sandbox.McelDomainAdapterRegistry.evaluateAdapterReadiness("git-tools");

        console.log(JSON.stringify({{
          missingRemote,
          diverged,
          confirmation,
          prepare,
          manual,
          stale,
          receipts,
          receiptEvidence,
          persisted: JSON.parse(storageData.get("mcel.git-tools.preflight-receipts.v1") || "[]"),
          readiness,
          executeIntentType: typeof adapter.executeIntent
        }}));
        """
    )
    result = run_node_json(script)

    assert result["missingRemote"]["decision"] == "block"
    assert result["missingRemote"]["receipt"]["status"] == "blocked"
    assert "remote-missing" in {
        blocker["code"] for blocker in result["missingRemote"]["blockers"]
    }

    assert result["diverged"]["decision"] == "block"
    assert "remote-diverged" in {
        blocker["code"] for blocker in result["diverged"]["blockers"]
    }

    assert result["confirmation"]["decision"] == "confirm"
    assert result["confirmation"]["confirmationRequired"] is True
    assert result["confirmation"]["receipt"]["status"] == "confirmation-required"
    assert "working-tree-dirty" in {
        warning["code"] for warning in result["confirmation"]["warnings"]
    }

    assert result["prepare"]["decision"] == "allow"
    assert result["prepare"]["receipt"]["status"] == "allowed"

    assert result["manual"]["decision"] == "block"
    assert "manual-command-policy-block" in {
        blocker["code"] for blocker in result["manual"]["blockers"]
    }

    assert result["stale"]["decision"] == "block"
    assert "state-stale" in {
        blocker["code"] for blocker in result["stale"]["blockers"]
    }

    assert len(result["receipts"]) == 6
    assert len(result["persisted"]) == 6
    assert len(result["receiptEvidence"]) == 6
    assert all(item["receiptBacked"] is True for item in result["receiptEvidence"])
    assert all(item["executionAttempted"] is False for item in result["receipts"])

    readiness = result["readiness"]
    assert readiness["semanticRuntimeReady"] is False
    assert readiness["adapterExecutable"] is True
    assert readiness["actionPlannerReady"] is True
    assert readiness["recoveryReady"] is True
    assert readiness["missingSemantics"] == []
    assert result["executeIntentType"] == "function"


def test_git_tools_preflight_blocks_unknown_unobserved_and_nothing_to_publish_states() -> None:
    registry_path = SCRIPTS / "mcel-domain-adapter-registry.js"
    adapter_path = SCRIPTS / "git-tools-semantic-adapter.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(adapter_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-adapter.js"}}
        );
        const adapter = sandbox.GitToolsSemanticAdapter;
        adapter.clearReceipts();

        const unknown = adapter.preflightIntent(
          "deleteEverything",
          adapter.getState(),
          {{now: "2026-07-15T19:00:00.000Z"}}
        );
        const unobserved = adapter.preflightIntent(
          "pushCurrentBranch",
          adapter.getState(),
          {{now: "2026-07-15T19:00:00.000Z"}}
        );
        const nothingToPushState = adapter.normalizeStatus({{
          ok: true,
          repo_dir: "C:/work/main_computer_test",
          git_root: "C:/work/main_computer_test",
          is_git_repo: true,
          has_head: true,
          branch: "main",
          ahead: 0,
          behind: 0,
          dirty: false,
          changed_count: 0,
          untracked_count: 0,
          remotes: [
            {{name: "origin", fetch: "https://example.invalid/repo.git", push: "https://example.invalid/repo.git"}}
          ]
        }}, {{observedAt: "2026-07-15T19:00:00.000Z"}});
        const nothingToPush = adapter.preflightIntent(
          "pushCurrentBranch",
          nothingToPushState,
          {{now: "2026-07-15T19:00:30.000Z"}}
        );

        console.log(JSON.stringify({{
          unknown,
          unobserved,
          nothingToPush,
          receipts: adapter.listReceipts()
        }}));
        """
    )
    result = run_node_json(script)

    assert "unsupported-intent" in {
        blocker["code"] for blocker in result["unknown"]["blockers"]
    }
    assert "state-not-observed" in {
        blocker["code"] for blocker in result["unobserved"]["blockers"]
    }
    assert "nothing-to-publish" in {
        blocker["code"] for blocker in result["nothingToPush"]["blockers"]
    }
    assert len(result["receipts"]) == 3
    assert all(receipt["status"] == "blocked" for receipt in result["receipts"])


def test_git_tools_executes_only_governed_refresh_and_emits_execution_receipt() -> None:
    registry_path = SCRIPTS / "mcel-domain-adapter-registry.js"
    adapter_path = SCRIPTS / "git-tools-semantic-adapter.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const storageData = new Map();
        const calls = [];
        const sandbox = {{console}};
        sandbox.window = sandbox;
        sandbox.localStorage = {{
          getItem(key) {{ return storageData.has(key) ? storageData.get(key) : null; }},
          setItem(key, value) {{ storageData.set(key, String(value)); }},
          removeItem(key) {{ storageData.delete(key); }}
        }};
        sandbox.GitToolsStatusApi = {{
          async fetchStatus(options) {{
            calls.push(options);
            return {{
              ok: true,
              repo_dir: options.repoDir,
              git_root: "C:/work/main_computer_test",
              is_git_repo: true,
              has_head: true,
              branch: "main",
              ahead: 1,
              behind: 0,
              dirty: false,
              changed_count: 0,
              untracked_count: 0,
              remotes: [
                {{name: "origin", fetch: "https://example.invalid/repo.git", push: "https://example.invalid/repo.git"}}
              ]
            }};
          }}
        }};

        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(adapter_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-adapter.js"}}
        );

        (async () => {{
          const adapter = sandbox.GitToolsSemanticAdapter;
          adapter.clearReceipts();

          const refresh = await adapter.executeIntent("refreshStatus", {{
            repoDir: "C:/work/main_computer_test",
            observedAt: "2026-07-15T20:00:00.000Z",
            now: "2026-07-15T20:00:00.000Z",
            completedAt: "2026-07-15T20:00:01.000Z"
          }});

          const deniedPush = await adapter.executeIntent("pushCurrentBranch", {{
            now: "2026-07-15T20:00:30.000Z"
          }});

          const receipts = adapter.listReceipts();
          const evidence = adapter.mapEvidence(adapter.getState());
          const executionEvidence = evidence.find(
            (entry) => entry.kind === "action-execution-receipt"
          );
          const readiness = sandbox.McelDomainAdapterRegistry
            .evaluateAdapterReadiness("git-tools");

          process.stdout.write(JSON.stringify({{
            calls,
            refresh,
            deniedPush,
            receipts,
            executionEvidence,
            readiness
          }}));
        }})().catch((error) => {{
          console.error(error && error.stack ? error.stack : error);
          process.exit(1);
        }});
        """
    )
    result = run_node_json(script)

    assert result["calls"] == [{"repoDir": "C:/work/main_computer_test"}]

    refresh = result["refresh"]
    assert refresh["intentId"] == "refreshStatus"
    assert refresh["status"] == "succeeded"
    assert refresh["executionAttempted"] is True
    assert refresh["executionBinding"] == "git-tools-status-api.fetchStatus"
    assert refresh["stateAfter"]["phase"] == "ready"
    assert refresh["stateAfter"]["branch"] == "main"
    assert refresh["receipt"]["kind"] == "action-execution-receipt"
    assert refresh["receipt"]["status"] == "succeeded"
    assert refresh["receipt"]["executionAttempted"] is True
    assert refresh["receipt"]["result"]["status"] == "succeeded"

    denied = result["deniedPush"]
    assert denied["decision"] == "block"
    assert denied["executionAttempted"] is False
    assert "preflight-required" in {
        blocker["code"] for blocker in denied["blockers"]
    }
    assert denied["receipt"]["kind"] == "action-execution-receipt"
    assert denied["receipt"]["executionAttempted"] is False

    assert len(result["receipts"]) == 2
    assert result["executionEvidence"]["receiptBacked"] is True
    assert result["executionEvidence"]["claims"]["executionAttempted"] is True
    assert (
        result["executionEvidence"]["claims"]["executionBinding"]
        == "git-tools-status-api.fetchStatus"
    )
    assert result["executionEvidence"]["claims"]["resultStatus"] == "succeeded"

    readiness = result["readiness"]
    assert readiness["semanticRuntimeReady"] is False
    assert readiness["adapterExecutable"] is True
    assert readiness["recoveryReady"] is True
    assert readiness["missingSemantics"] == []


def test_git_tools_refresh_execution_failure_is_receipted_without_mutation_fallback() -> None:
    registry_path = SCRIPTS / "mcel-domain-adapter-registry.js"
    adapter_path = SCRIPTS / "git-tools-semantic-adapter.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(adapter_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-adapter.js"}}
        );

        (async () => {{
          const adapter = sandbox.GitToolsSemanticAdapter;
          adapter.clearReceipts();
          const result = await adapter.executeIntent("refreshStatus", {{
            repoDir: "C:/offline",
            now: "2026-07-15T20:00:00.000Z",
            completedAt: "2026-07-15T20:00:01.000Z",
            api: {{
              async fetchStatus() {{
                throw new Error("status backend offline");
              }}
            }}
          }});
          process.stdout.write(JSON.stringify({{
            result,
            receipts: adapter.listReceipts(),
            readiness: sandbox.McelDomainAdapterRegistry.evaluateAdapterReadiness("git-tools")
          }}));
        }})().catch((error) => {{
          console.error(error && error.stack ? error.stack : error);
          process.exit(1);
        }});
        """
    )
    result = run_node_json(script)

    execution = result["result"]
    assert execution["status"] == "failed"
    assert execution["executionAttempted"] is True
    assert execution["stateAfter"]["phase"] == "error"
    assert execution["error"] == {
        "code": "git-status-request-failed",
        "message": "status backend offline",
    }
    assert execution["receipt"]["kind"] == "action-execution-receipt"
    assert execution["receipt"]["status"] == "failed"
    assert execution["receipt"]["executionAttempted"] is True
    assert execution["receipt"]["error"]["message"] == "status backend offline"
    assert len(result["receipts"]) == 1
    assert result["readiness"]["semanticRuntimeReady"] is False
    assert result["readiness"]["adapterExecutable"] is True
    assert result["readiness"]["recoveryReady"] is True


def test_git_tools_recovery_classifier_attaches_guidance_without_promoting_readiness() -> None:
    registry_path = SCRIPTS / "mcel-domain-adapter-registry.js"
    adapter_path = SCRIPTS / "git-tools-semantic-adapter.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(adapter_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-adapter.js"}}
        );
        const adapter = sandbox.GitToolsSemanticAdapter;
        const state = adapter.normalizeStatus({{
          ok: true,
          repo_dir: "C:/work/main_computer_test",
          git_root: "C:/work/main_computer_test",
          is_git_repo: true,
          has_head: true,
          branch: "main",
          ahead: 0,
          behind: 0,
          dirty: true,
          changed_count: 2,
          untracked_count: 1,
          remotes: [
            {{name: "origin", fetch: "https://example.invalid/repo.git", push: "https://example.invalid/repo.git"}}
          ]
        }}, {{
          observedAt: "2026-07-15T22:00:00.000Z"
        }});
        const push = adapter.preflightIntent("pushCurrentBranch", state, {{
          now: "2026-07-15T22:00:30.000Z",
          store: false
        }});
        const failedRefresh = adapter.classifyFailure({{
          receiptId: "refresh-failure-receipt",
          intentId: "refreshStatus",
          kind: "action-execution-receipt",
          error: {{
            code: "git-status-request-failed",
            message: "status backend offline"
          }}
        }}, {{
          ...state,
          phase: "error",
          ok: false,
          error: {{
            code: "git-status-request-failed",
            message: "status backend offline"
          }}
        }}, {{
          now: "2026-07-15T22:01:00.000Z"
        }});
        const failedRecovery = adapter.buildRecoveryOptions(
          failedRefresh,
          state,
          {{now: "2026-07-15T22:01:00.000Z"}}
        );
        console.log(JSON.stringify({{
          pushReceipt: push.receipt,
          failedRefresh,
          failedRecovery,
          coverage: adapter.getRecoveryCoverage(),
          readiness: sandbox.McelDomainAdapterRegistry.evaluateAdapterReadiness("git-tools")
        }}));
        """
    )
    result = run_node_json(script)

    push_receipt = result["pushReceipt"]
    assert push_receipt["recoveryClassified"] is True
    assert push_receipt["failure"]["failureClass"] == "nothing-to-publish"
    assert push_receipt["failure"]["severity"] == "informational"
    assert push_receipt["failure"]["retrySafe"] is False
    assert push_receipt["recovery"]["recommendedNextStep"].startswith(
        "Inspect the working tree"
    )
    assert [option["intentId"] for option in push_receipt["recovery"]["options"]] == [
        "inspectWorkingTree",
        "commitChanges",
    ]
    assert push_receipt["recovery"]["prohibitedActions"] == ["pushCurrentBranch"]
    assert push_receipt["executionAttempted"] is False

    failed = result["failedRefresh"]
    assert failed["failureClass"] == "status-refresh-failed"
    assert failed["severity"] == "blocking"
    assert failed["retrySafe"] is True
    assert failed["refreshRequired"] is True
    assert failed["mutationAllowed"] is False

    recovery = result["failedRecovery"]
    assert recovery["failureClass"] == "status-refresh-failed"
    assert recovery["options"][0] == {
        "intentId": "refreshStatus",
        "label": "Execute governed status refresh",
        "kind": "governed-execution",
        "executable": True,
        "safe": True,
    }
    assert "pushCurrentBranch" in recovery["prohibitedActions"]

    coverage = result["coverage"]
    assert coverage["classificationReady"] is True
    assert coverage["guidanceReady"] is True
    assert coverage["coverageReady"] is True
    assert coverage["coveredFailureClasses"] == coverage["requiredFailureClasses"]
    assert coverage["unverifiedFailureClasses"] == []
    assert coverage["verificationMode"] == "derived-runtime-audit"
    assert coverage["verification"]["passed"] is True
    assert all(coverage["verification"]["checks"].values())
    assert coverage["missingDefinitions"] == []
    assert coverage["invalidDefinitions"] == []
    assert coverage["missingGuidance"] == []
    assert coverage["invalidGuidance"] == []
    assert coverage["invalidSourceMappings"] == []

    readiness = result["readiness"]
    assert readiness["recoveryClassifierPresent"] is True
    assert readiness["recoveryCoverageReady"] is True
    assert readiness["recoveryReady"] is True
    assert readiness["semanticRuntimeReady"] is False
    assert readiness["missingSemantics"] == []


def test_failed_governed_refresh_receipt_contains_recovery_guidance() -> None:
    registry_path = SCRIPTS / "mcel-domain-adapter-registry.js"
    adapter_path = SCRIPTS / "git-tools-semantic-adapter.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(adapter_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-adapter.js"}}
        );
        (async () => {{
          const adapter = sandbox.GitToolsSemanticAdapter;
          const result = await adapter.executeIntent("refreshStatus", {{
            repoDir: "C:/broken",
            now: "2026-07-15T22:05:00.000Z",
            completedAt: "2026-07-15T22:05:01.000Z",
            store: false,
            api: {{
              async fetchStatus() {{
                throw new Error("status backend offline");
              }}
            }}
          }});
          console.log(JSON.stringify(result));
        }})().catch((error) => {{
          console.error(error && error.stack ? error.stack : error);
          process.exit(1);
        }});
        """
    )
    result = run_node_json(script)
    receipt = result["receipt"]
    assert result["status"] == "failed"
    assert receipt["executionAttempted"] is True
    assert receipt["recoveryClassified"] is True
    assert receipt["failure"]["failureClass"] == "status-refresh-failed"
    assert receipt["recovery"]["retrySafe"] is True
    assert receipt["recovery"]["refreshRequired"] is True
    assert receipt["recovery"]["options"][0]["intentId"] == "refreshStatus"
    assert receipt["recovery"]["options"][0]["executable"] is True


def test_git_tools_preflight_suppresses_speculative_downstream_blockers() -> None:
    registry_path = SCRIPTS / "mcel-domain-adapter-registry.js"
    adapter_path = SCRIPTS / "git-tools-semantic-adapter.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(adapter_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-adapter.js"}}
        );
        const adapter = sandbox.GitToolsSemanticAdapter;
        const now = "2026-07-15T22:30:00.000Z";
        const unobserved = adapter.evaluatePreflight(
          "pushCurrentBranch",
          adapter.getState(),
          {{now}}
        );
        const notRepo = adapter.evaluatePreflight(
          "pushCurrentBranch",
          adapter.normalizeStatus({{
            ok: true,
            repo_dir: "C:/not-a-repo",
            is_git_repo: false,
            has_head: false,
            branch: "unknown",
            remotes: []
          }}, {{observedAt: "2026-07-15T22:29:30.000Z"}}),
          {{now}}
        );
        const missingRemote = adapter.evaluatePreflight(
          "pushCurrentBranch",
          adapter.normalizeStatus({{
            ok: true,
            repo_dir: "C:/repo",
            git_root: "C:/repo",
            is_git_repo: true,
            has_head: true,
            branch: "main",
            ahead: 1,
            behind: 0,
            remotes: []
          }}, {{observedAt: "2026-07-15T22:29:30.000Z"}}),
          {{now}}
        );
        const detached = adapter.evaluatePreflight(
          "pushCurrentBranch",
          adapter.normalizeStatus({{
            ok: true,
            repo_dir: "C:/repo",
            git_root: "C:/repo",
            is_git_repo: true,
            has_head: true,
            branch: "detached-or-unknown",
            ahead: null,
            behind: null,
            remotes: [
              {{name: "origin", fetch: "https://example.invalid/repo.git", push: "https://example.invalid/repo.git"}}
            ]
          }}, {{observedAt: "2026-07-15T22:29:30.000Z"}}),
          {{now}}
        );
        console.log(JSON.stringify({{
          unobserved: unobserved.blockers.map((entry) => entry.code),
          notRepo: notRepo.blockers.map((entry) => entry.code),
          missingRemote: missingRemote.blockers.map((entry) => entry.code),
          detached: detached.blockers.map((entry) => entry.code)
        }}));
        """
    )
    result = run_node_json(script)
    assert result == {
        "unobserved": ["state-not-observed"],
        "notRepo": ["not-a-git-repository"],
        "missingRemote": ["remote-missing"],
        "detached": ["branch-unpublishable"],
    }


def test_governed_local_gitea_push_executes_after_confirmation_and_revalidation() -> None:
    registry_path = SCRIPTS / "mcel-domain-adapter-registry.js"
    adapter_path = SCRIPTS / "git-tools-semantic-adapter.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const storageData = new Map();
        const sandbox = {{console}};
        sandbox.window = sandbox;
        sandbox.localStorage = {{
          getItem(key) {{ return storageData.has(key) ? storageData.get(key) : null; }},
          setItem(key, value) {{ storageData.set(key, String(value)); }},
          removeItem(key) {{ storageData.delete(key); }}
        }};
        const statuses = [
          {{ahead: 1, observed: "initial"}},
          {{ahead: 1, observed: "revalidated"}},
          {{ahead: 0, observed: "post-push"}}
        ];
        let statusCalls = 0;
        sandbox.GitToolsStatusApi = {{
          async fetchStatus(options) {{
            const item = statuses[Math.min(statusCalls, statuses.length - 1)];
            statusCalls += 1;
            return {{
              ok: true,
              repo_dir: options.repoDir,
              git_root: "C:/work/main_computer_test",
              is_git_repo: true,
              has_head: true,
              branch: "main",
              ahead: item.ahead,
              behind: 0,
              dirty: false,
              changed_count: 0,
              untracked_count: 0,
              remotes: [
                {{
                  name: "local-gitea",
                  fetch: "http://localhost:3000/local/main-computer.git",
                  push: "http://localhost:3000/local/main-computer.git"
                }}
              ]
            }};
          }}
        }};

        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(adapter_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-adapter.js"}}
        );

        (async () => {{
          const adapter = sandbox.GitToolsSemanticAdapter;
          adapter.clearReceipts();
          await adapter.executeIntent("refreshStatus", {{
            repoDir: "C:/work/main_computer_test",
            now: "2026-07-15T23:00:00.000Z",
            observedAt: "2026-07-15T23:00:00.000Z"
          }});
          const parameters = {{
            repoDir: "C:/work/main_computer_test",
            remote: "local-gitea",
            owner: "local",
            repo: "main-computer",
            protocol: "http",
            switchOrigin: false
          }};
          const preflight = adapter.preflightIntent(
            "pushCurrentBranch",
            adapter.getState(),
            {{
              now: "2026-07-15T23:00:10.000Z",
              parameters
            }}
          );
          let backendCalls = 0;
          let executionContext = null;
          const execution = await adapter.executeIntent(
            "pushCurrentBranch",
            {{
              preflight,
              confirmation: {{
                accepted: true,
                confirmedAt: "2026-07-15T23:00:20.000Z"
              }},
              now: "2026-07-15T23:00:20.000Z",
              revalidationNow: "2026-07-15T23:00:20.000Z",
              revalidatedAt: "2026-07-15T23:00:20.000Z",
              postObservedAt: "2026-07-15T23:00:30.000Z",
              completedAt: "2026-07-15T23:00:30.000Z",
              executeBinding: async (context) => {{
                backendCalls += 1;
                executionContext = context;
                return {{
                  ok: true,
                  remote: "local-gitea",
                  operation: {{id: "operation-1", status: "succeeded"}}
                }};
              }}
            }}
          );
          const receipts = adapter.listReceipts();
          const readiness = sandbox.McelDomainAdapterRegistry
            .evaluateAdapterReadiness("git-tools");
          process.stdout.write(JSON.stringify({{
            statusCalls,
            backendCalls,
            executionContext,
            preflight,
            execution,
            receipts,
            readiness
          }}));
        }})().catch((error) => {{
          console.error(error && error.stack ? error.stack : error);
          process.exit(1);
        }});
        """
    )
    result = run_node_json(script)

    assert result["statusCalls"] == 3
    assert result["backendCalls"] == 1
    assert result["preflight"]["decision"] == "confirm"
    assert result["preflight"]["executionAvailable"] is True
    assert (
        result["preflight"]["executionBinding"]
        == "git-tools-server-panel.serverPushLocal"
    )
    assert result["executionContext"]["parameters"]["remote"] == "local-gitea"
    assert result["execution"]["status"] == "succeeded"
    assert result["execution"]["executionAttempted"] is True
    assert result["execution"]["stateBefore"]["ahead"] == 1
    assert result["execution"]["stateAfter"]["ahead"] == 0

    receipt = result["execution"]["receipt"]
    assert receipt["kind"] == "action-execution-receipt"
    assert receipt["status"] == "succeeded"
    assert receipt["executionAttempted"] is True
    assert receipt["executionBinding"] == "git-tools-server-panel.serverPushLocal"
    assert receipt["preflightReceiptId"] == result["preflight"]["receipt"]["receiptId"]
    assert (
        receipt["confirmationReceiptId"]
        == result["execution"]["confirmationReceipt"]["receiptId"]
    )
    assert receipt["parentReceiptId"] == receipt["confirmationReceiptId"]
    assert receipt["result"]["operationId"] == "operation-1"
    assert receipt["result"]["remote"] == "local-gitea"
    assert receipt["error"] is None

    assert [(item["kind"], item["status"]) for item in result["receipts"]] == [
        ("action-execution-receipt", "succeeded"),
        ("preflight-decision-receipt", "confirmation-required"),
        ("confirmation-decision-receipt", "confirmed"),
        ("action-execution-receipt", "succeeded"),
    ]
    readiness = result["readiness"]
    assert readiness["semanticRuntimeReady"] is False
    assert readiness["runtimeCoreReady"] is True
    assert readiness["semanticRuntimeScope"] == "governed-publish-partial"
    assert readiness["executableIntentCount"] == 2
    assert readiness["preflightOnlyIntentCount"] == 1


def test_governed_push_decline_and_changed_state_never_call_backend() -> None:
    registry_path = SCRIPTS / "mcel-domain-adapter-registry.js"
    adapter_path = SCRIPTS / "git-tools-semantic-adapter.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        const statuses = [1, 2];
        let statusCalls = 0;
        sandbox.GitToolsStatusApi = {{
          async fetchStatus(options) {{
            const ahead = statuses[Math.min(statusCalls, statuses.length - 1)];
            statusCalls += 1;
            return {{
              ok: true,
              repo_dir: options.repoDir,
              git_root: "C:/work/main_computer_test",
              is_git_repo: true,
              has_head: true,
              branch: "main",
              ahead,
              behind: 0,
              dirty: false,
              changed_count: 0,
              untracked_count: 0,
              remotes: [
                {{
                  name: "local-gitea",
                  fetch: "http://localhost:3000/local/main-computer.git",
                  push: "http://localhost:3000/local/main-computer.git"
                }}
              ]
            }};
          }}
        }};
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(adapter_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-adapter.js"}}
        );

        (async () => {{
          const adapter = sandbox.GitToolsSemanticAdapter;
          adapter.clearReceipts();
          await adapter.executeIntent("refreshStatus", {{
            repoDir: "C:/work/main_computer_test",
            now: "2026-07-15T23:10:00.000Z",
            observedAt: "2026-07-15T23:10:00.000Z"
          }});
          const parameters = {{
            repoDir: "C:/work/main_computer_test",
            remote: "local-gitea",
            owner: "local",
            repo: "main-computer",
            protocol: "http"
          }};
          const preflight = adapter.preflightIntent(
            "pushCurrentBranch",
            adapter.getState(),
            {{
              now: "2026-07-15T23:10:10.000Z",
              parameters
            }}
          );
          let backendCalls = 0;
          const executeBinding = async () => {{
            backendCalls += 1;
            return {{ok: true}};
          }};
          const declined = await adapter.executeIntent(
            "pushCurrentBranch",
            {{
              preflight,
              confirmation: {{accepted: false}},
              now: "2026-07-15T23:10:20.000Z",
              executeBinding
            }}
          );
          const changed = await adapter.executeIntent(
            "pushCurrentBranch",
            {{
              preflight,
              confirmation: {{
                accepted: true,
                confirmedAt: "2026-07-15T23:10:30.000Z"
              }},
              now: "2026-07-15T23:10:30.000Z",
              revalidationNow: "2026-07-15T23:10:30.000Z",
              revalidatedAt: "2026-07-15T23:10:30.000Z",
              executeBinding
            }}
          );
          process.stdout.write(JSON.stringify({{
            statusCalls,
            backendCalls,
            declined,
            changed,
            receipts: adapter.listReceipts()
          }}));
        }})().catch((error) => {{
          console.error(error && error.stack ? error.stack : error);
          process.exit(1);
        }});
        """
    )
    result = run_node_json(script)

    assert result["statusCalls"] == 2
    assert result["backendCalls"] == 0

    declined = result["declined"]
    assert declined["status"] == "cancelled"
    assert declined["executionAttempted"] is False
    assert declined["receipt"]["kind"] == "confirmation-decision-receipt"
    assert declined["receipt"]["decision"] == "decline"
    assert declined["receipt"]["failure"]["failureClass"] == "confirmation-declined"

    changed = result["changed"]
    assert changed["status"] == "blocked"
    assert changed["executionAttempted"] is False
    assert changed["receipt"]["kind"] == "action-execution-receipt"
    assert changed["receipt"]["failure"]["failureClass"] == "state-changed-after-preflight"
    assert {
        blocker["code"] for blocker in changed["blockers"]
    } == {"state-changed-after-preflight"}


def test_governed_push_rejects_preflight_receipt_missing_from_ledger() -> None:
    registry_path = SCRIPTS / "mcel-domain-adapter-registry.js"
    adapter_path = SCRIPTS / "git-tools-semantic-adapter.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        let statusCalls = 0;
        sandbox.GitToolsStatusApi = {{
          async fetchStatus(options) {{
            statusCalls += 1;
            return {{
              ok: true,
              repo_dir: options.repoDir,
              git_root: "C:/work/main_computer_test",
              is_git_repo: true,
              has_head: true,
              branch: "main",
              ahead: 1,
              behind: 0,
              dirty: false,
              changed_count: 0,
              untracked_count: 0,
              remotes: [{{
                name: "local-gitea",
                fetch: "http://localhost:3000/local/main-computer.git",
                push: "http://localhost:3000/local/main-computer.git"
              }}]
            }};
          }}
        }};
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(adapter_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-adapter.js"}}
        );

        (async () => {{
          const adapter = sandbox.GitToolsSemanticAdapter;
          await adapter.executeIntent("refreshStatus", {{
            repoDir: "C:/work/main_computer_test",
            now: "2026-07-15T23:10:00.000Z",
            observedAt: "2026-07-15T23:10:00.000Z"
          }});
          const preflight = adapter.preflightIntent(
            "pushCurrentBranch",
            adapter.getState(),
            {{
              now: "2026-07-15T23:10:10.000Z",
              parameters: {{
                repoDir: "C:/work/main_computer_test",
                remote: "local-gitea",
                owner: "local",
                repo: "main-computer",
                protocol: "http"
              }}
            }}
          );
          adapter.clearReceipts();
          let backendCalls = 0;
          const execution = await adapter.executeIntent(
            "pushCurrentBranch",
            {{
              preflight,
              confirmation: {{accepted: true}},
              now: "2026-07-15T23:10:20.000Z",
              executeBinding: async () => {{
                backendCalls += 1;
                return {{ok: true}};
              }}
            }}
          );
          process.stdout.write(JSON.stringify({{
            statusCalls,
            backendCalls,
            execution
          }}));
        }})().catch((error) => {{
          console.error(error && error.stack ? error.stack : error);
          process.exit(1);
        }});
        """
    )
    result = run_node_json(script)
    assert result["statusCalls"] == 1
    assert result["backendCalls"] == 0
    assert result["execution"]["status"] == "blocked"
    assert result["execution"]["executionAttempted"] is False
    assert [item["code"] for item in result["execution"]["blockers"]] == [
        "preflight-required"
    ]
    assert "stored confirmation-required" in (
        result["execution"]["blockers"][0]["message"]
    )
