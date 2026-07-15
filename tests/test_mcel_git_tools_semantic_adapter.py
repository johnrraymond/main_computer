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
              buildReceipt: typeof adapter.buildReceipt
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
    assert result["adapterKind"] == "preflight-domain-adapter"
    assert result["registeredAdapters"] == [
        {
            "id": "git-tools-domain-adapter",
            "appId": "git-tools",
            "version": "git-tools-semantic-adapter-preflight-v2",
            "kind": "preflight-domain-adapter",
        }
    ]

    readiness = result["readiness"]
    assert readiness["semanticRuntimeReady"] is False
    assert readiness["adapterExecutable"] is False
    assert readiness["stateMachineReady"] is True
    assert readiness["actionPlannerReady"] is True
    assert readiness["capabilityProviderReady"] is True
    assert readiness["recoveryReady"] is False
    assert readiness["missingSemantics"] == [
        "adapterExecutable",
        "recoveryReady",
    ]

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
    assert result["refresh"]["executable"] is False
    assert result["push"]["available"] is True
    assert result["push"]["risk"] == "publish-mutation"
    assert "run preflight" in result["push"]["blockedReason"]
    assert all(item["receiptBacked"] is False for item in result["evidence"])
    assert result["methodPresence"] == {
        "executeIntent": "undefined",
        "preflightIntent": "function",
        "buildReceipt": "function",
    }


def test_git_tools_adapter_stays_domain_enrichment_only_under_planner_truth_gate() -> None:
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
    assert readiness["semanticRuntimeStatus"] == "domain-enrichment-only"
    assert readiness["adapterKind"] == "preflight-domain-adapter"
    assert readiness["adapterExecutable"] is False
    assert readiness["stateMachineReady"] is True
    assert readiness["actionPlannerReady"] is True
    assert readiness["capabilityProviderReady"] is True
    assert readiness["recoveryReady"] is False

    assert result["snapshot"] == {
        "stateSnapshotAvailable": True,
        "intentListAvailable": True,
        "statePhase": "uninitialized",
        "firstIntent": "refreshStatus",
    }


def test_git_tools_adapter_captures_status_api_failure_without_promoting_readiness() -> None:
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
    assert readiness["adapterExecutable"] is False
    assert readiness["actionPlannerReady"] is True
    assert readiness["recoveryReady"] is False
    assert readiness["missingSemantics"] == ["adapterExecutable", "recoveryReady"]
    assert result["executeIntentType"] == "undefined"


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
