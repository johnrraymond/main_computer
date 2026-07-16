from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_APP = PROJECT_ROOT / "main_computer/web/applications"
APPLICATIONS_HTML = (PROJECT_ROOT / "main_computer/web/applications.html").read_text(
    encoding="utf-8"
)
GIT_TOOLS_HTML = (WEB_APP / "apps/git-tools.html").read_text(encoding="utf-8")
GIT_TOOLS_CSS = (WEB_APP / "styles/git-tools.css").read_text(encoding="utf-8")
LAYOUT_JS = (WEB_APP / "scripts/git-tools-layout-contract.js").read_text(
    encoding="utf-8"
)
PANEL_JS = (WEB_APP / "scripts/git-tools-semantic-panel.js").read_text(
    encoding="utf-8"
)
SERVER_PANEL_JS = (WEB_APP / "scripts/git-tools-server-panel.js").read_text(
    encoding="utf-8"
)


def run_node_json(script: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_semantic_panel_is_loaded_after_adapter_and_authored_as_support_surface() -> None:
    adapter = "<!-- @include applications/scripts/git-tools-semantic-adapter.js -->"
    panel = "<!-- @include applications/scripts/git-tools-semantic-panel.js -->"
    planner = "<!-- @include applications/scripts/mcel-specimen-planner.js -->"

    for include in (adapter, panel, planner):
        assert include in APPLICATIONS_HTML

    assert APPLICATIONS_HTML.index(adapter) < APPLICATIONS_HTML.index(panel)
    assert APPLICATIONS_HTML.index(panel) < APPLICATIONS_HTML.index(planner)

    expected_html = (
        'data-git-layout-support="semantics"',
        'id="git-semantic-runtime-panel"',
        'data-git-support-panel="semantics"',
        'id="git-semantic-refresh-state"',
        'id="git-semantic-run-push-preflight"',
        'id="git-semantic-view-latest-receipt"',
        'id="git-semantic-clear-receipts"',
        'id="git-semantic-execution-enabled">Refresh + governed push</dd>',
        'id="git-semantic-runtime-core-ready">Ready</dd>',
        'id="git-semantic-application-coverage">Partial</dd>',
        'id="git-semantic-runtime-scope">Governed Publish Partial</dd>',
        'id="git-semantic-intent-coverage-heading"',
        'id="git-semantic-intent-coverage-summary"',
        'id="git-semantic-intent-coverage-matrix"',
        "2 executable · 1 preflight only · 3 declared only · 1 prohibited",
        "inspectWorkingTree     | Declared only",
        "runManualCommand       | Prohibited",
        'id="git-server-push-local"',
        'data-mcel-intent="pushCurrentBranch"',
        'data-mcel-execution-policy="governed-confirmed-execution"',
        "Push to Local Gitea",
        "Execute status refresh",
        "Force-push and arbitrary commands remain disabled.",
        'id="git-semantic-recovery-guidance"',
        'id="git-semantic-recovery-class"',
        'id="git-semantic-recovery-next-step"',
        'id="git-semantic-recovery-options"',
        "Verified coverage",
    )
    for snippet in expected_html:
        assert snippet in GIT_TOOLS_HTML


def test_layout_contract_recognizes_and_focuses_semantics_support_view() -> None:
    source_path = WEB_APP / "scripts/git-tools-layout-contract.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.globalThis = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(source_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-layout-contract.js"}}
        );
        const api = sandbox.MainComputerGitToolsLayout;
        const authored = {{
          complete: true,
          missing: [],
          mismatches: [],
          units: api.SAFE_DEFAULTS
        }};
        const result = api.resolveLayout({{
          viewport: {{width: 900, height: 900}},
          authored,
          preferences: api.normalizePreferences(api.DEFAULT_PREFERENCES, authored),
          phase: "proof-review",
          supportView: "semantics",
          activeSurface: "support",
          supportOpen: true
        }});
        process.stdout.write(JSON.stringify({{
          supportView: result.supportView,
          activeSurface: result.activeSurface,
          support: result.actual.support
        }}));
        """
    )
    result = run_node_json(script)
    assert result == {
        "supportView": "semantics",
        "activeSurface": "support",
        "support": "tab",
    }
    assert 'semantics: "#git-semantic-runtime-panel"' in LAYOUT_JS
    assert '["evidence", "semantics"].includes(state.supportView)' in LAYOUT_JS


def test_semantic_panel_builds_visible_safe_read_execution_view_model() -> None:
    source_path = WEB_APP / "scripts/git-tools-semantic-panel.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(source_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-panel.js"}}
        );
        const receipts = [{{
          receiptId: "receipt-1",
          intentId: "pushCurrentBranch",
          status: "blocked",
          decision: "block",
          createdAt: "2026-07-15T20:00:30.000Z",
          stateFingerprint: "fnv1a-test",
          blockers: [{{
            code: "remote-diverged",
            message: "Local and remote history have diverged."
          }}],
          warnings: [],
          executionAttempted: false
        }}];
        const adapter = {{
          getState() {{
            return {{
              phase: "ready",
              observedAt: "2026-07-15T20:00:00.000Z",
              repoDir: "C:/work/main_computer_test",
              gitRoot: "C:/work/main_computer_test",
              branch: "main",
              ahead: 2,
              behind: 1
            }};
          }},
          listReceipts() {{ return receipts; }}
        }};
        const registry = {{
          evaluateAdapterReadiness() {{
            return {{
              registryAdapterPresent: true,
              semanticRuntimeReady: false,
              actionPlannerReady: true,
              adapterExecutable: true,
              recoveryReady: false
            }};
          }}
        }};
        const api = sandbox.GitToolsSemanticPanel;
        const model = api.buildViewModel({{
          adapter,
          registry,
          now: Date.parse("2026-07-15T20:00:30.000Z")
        }});
        process.stdout.write(JSON.stringify({{
          model,
          receiptText: api.receiptText(receipts[0])
        }}));
        """
    )
    result = run_node_json(script)
    model = result["model"]
    assert model["runtimeStatus"] == "Safe-read execution"
    assert model["freshness"] == "Fresh"
    assert model["repository"] == "C:/work/main_computer_test"
    assert model["branch"] == "main"
    assert model["divergence"] == "2 / 1"
    assert model["pushDecision"].startswith("Blocked:")
    assert model["receiptCount"] == 1
    assert model["executionEnabled"] == "Refresh only"
    assert model["latestReceipt"]["executionAttempted"] is False
    assert "Execution attempted: no" in result["receiptText"]
    assert "remote-diverged" in result["receiptText"]


def test_semantic_panel_uses_only_governed_refresh_execution_and_controls_visibility() -> None:
    assert 'executeIntent("refreshStatus")' in PANEL_JS
    assert 'preflightIntent(PUSH_INTENT_ID, adapter.getState())' in PANEL_JS
    assert "No Git mutation was executed." in PANEL_JS
    assert '[data-git-layout-support-view="semantics"]' in GIT_TOOLS_CSS
    assert '[data-git-support-panel="semantics"]' in GIT_TOOLS_CSS
    assert ".git-semantic-runtime-panel" in GIT_TOOLS_CSS
    assert ".git-semantic-receipt-output" in GIT_TOOLS_CSS
    assert ".git-semantic-recovery-guidance" in GIT_TOOLS_CSS
    assert ".git-semantic-recovery-options" in GIT_TOOLS_CSS
    assert ".git-semantic-intent-coverage" in GIT_TOOLS_CSS
    assert ".git-semantic-intent-coverage-matrix" in GIT_TOOLS_CSS


def test_semantic_actions_preserve_selected_support_view_and_own_clicks() -> None:
    source_path = WEB_APP / "scripts/git-tools-semantic-panel.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(source_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-panel.js"}}
        );

        (async () => {{
        function makeNode() {{
          return {{
            dataset: {{}},
            textContent: "",
            disabled: false,
            attributes: {{}},
            handlers: {{}},
            setAttribute(name, value) {{ this.attributes[name] = String(value); }},
            addEventListener(type, handler) {{ this.handlers[type] = handler; }}
          }};
        }}

        const selectors = [
          "#git-semantic-runtime-panel",
          "#git-semantic-runtime-status",
          "#git-semantic-state-freshness",
          "#git-semantic-repository",
          "#git-semantic-branch",
          "#git-semantic-divergence",
          "#git-semantic-push-decision",
          "#git-semantic-receipt-count",
          "#git-semantic-execution-enabled",
          "#git-semantic-refresh-state",
          "#git-semantic-run-push-preflight",
          "#git-semantic-view-latest-receipt",
          "#git-semantic-clear-receipts",
          "#git-semantic-runtime-message",
          "#git-semantic-receipt-output"
        ];
        const nodes = Object.fromEntries(selectors.map((selector) => [selector, makeNode()]));
        let layoutState = {{
          supportView: "semantics",
          activeSurface: "support",
          supportOpen: true
        }};
        const selections = [];
        const layoutController = {{
          get resolved() {{ return {{...layoutState}}; }},
          selectSupport(view) {{
            selections.push(view);
            layoutState = {{
              supportView: view,
              activeSurface: "support",
              supportOpen: true
            }};
            return {{ok: true, resolved: {{...layoutState}}}};
          }}
        }};
        const root = {{
          querySelector(selector) {{ return nodes[selector] || null; }},
          __mcelGitToolsLayoutController: layoutController
        }};
        const documentObject = {{
          querySelector(selector) {{ return selector === "#git-tools-app" ? root : null; }}
        }};

        const receipts = [];
        const executeCalls = [];
        const state = {{
          phase: "ready",
          observedAt: "2026-07-15T20:00:00.000Z",
          repoDir: "C:/work/main_computer_test",
          gitRoot: "C:/work/main_computer_test",
          branch: "main",
          ahead: 0,
          behind: 0
        }};
        const adapter = {{
          getState() {{ return state; }},
          listReceipts() {{ return receipts.slice(); }},
          async executeIntent(intentId) {{
            executeCalls.push(intentId);
            layoutState = {{
              supportView: "server",
              activeSurface: "workflow",
              supportOpen: false
            }};
            const receipt = {{
              receiptId: "receipt-refresh",
              kind: "action-execution-receipt",
              intentId,
              status: "succeeded",
              decision: "allow",
              createdAt: "2026-07-15T20:00:00.500Z",
              stateFingerprint: "fnv1a-refresh",
              executionAttempted: true,
              executionBinding: "git-tools-status-api.fetchStatus",
              result: {{status: "succeeded"}},
              blockers: [],
              warnings: []
            }};
            receipts.push(receipt);
            return {{
              status: "succeeded",
              executionAttempted: true,
              stateAfter: state,
              receipt
            }};
          }},
          preflightIntent() {{
            layoutState = {{
              supportView: "server",
              activeSurface: "workflow",
              supportOpen: false
            }};
            const receipt = {{
              receiptId: "receipt-1",
              intentId: "pushCurrentBranch",
              status: "blocked",
              decision: "block",
              createdAt: "2026-07-15T20:00:01.000Z",
              stateFingerprint: "fnv1a-test",
              blockers: [{{code: "nothing-to-publish", message: "Nothing to publish."}}],
              warnings: [],
              executionAttempted: false
            }};
            receipts.push(receipt);
            return {{decision: "block", receipt}};
          }},
          clearReceipts() {{
            receipts.length = 0;
            layoutState = {{
              supportView: "server",
              activeSurface: "workflow",
              supportOpen: false
            }};
          }}
        }};
        const registry = {{
          evaluateAdapterReadiness() {{
            return {{
              registryAdapterPresent: true,
              semanticRuntimeReady: false,
              actionPlannerReady: true,
              adapterExecutable: true,
              recoveryReady: false
            }};
          }}
        }};

        const controller = sandbox.GitToolsSemanticPanel.mount({{
          document: documentObject,
          root,
          adapter,
          registry
        }});

        async function click(selector) {{
          const event = {{
            defaultPrevented: false,
            propagationStopped: false,
            preventDefault() {{ this.defaultPrevented = true; }},
            stopPropagation() {{ this.propagationStopped = true; }}
          }};
          const result = nodes[selector].handlers.click(event);
          if (result && typeof result.then === "function") await result;
          if (!event.propagationStopped) {{
            layoutState = {{
              supportView: "server",
              activeSurface: "workflow",
              supportOpen: false
            }};
          }}
          return {{
            defaultPrevented: event.defaultPrevented,
            propagationStopped: event.propagationStopped,
            supportView: layoutState.supportView,
            activeSurface: layoutState.activeSurface,
            supportOpen: layoutState.supportOpen
          }};
        }}

        const results = [];
        results.push(await click("#git-semantic-refresh-state"));
        results.push(await click("#git-semantic-run-push-preflight"));
        results.push(await click("#git-semantic-view-latest-receipt"));
        results.push(await click("#git-semantic-clear-receipts"));

        process.stdout.write(JSON.stringify({{
          results,
          selections,
          executeCalls,
          finalState: layoutState,
          version: controller.version
        }}));
        }})();
        """
    )
    result = run_node_json(script)
    assert result["version"] == "git-tools-semantic-panel-v8"
    assert len(result["results"]) == 4
    assert result["executeCalls"] == ["refreshStatus"]
    for action in result["results"]:
        assert action == {
            "defaultPrevented": True,
            "propagationStopped": True,
            "supportView": "semantics",
            "activeSurface": "support",
            "supportOpen": True,
        }
    assert result["finalState"] == {
        "supportView": "semantics",
        "activeSurface": "support",
        "supportOpen": True,
    }
    assert result["selections"]
    assert set(result["selections"]) == {"semantics"}


def test_existing_push_control_routes_through_governed_execution_gate() -> None:
    start = SERVER_PANEL_JS.index("async function pushLocalGitServerRemote()")
    end = SERVER_PANEL_JS.index("\nfunction useExternalGitRemoteDirect()", start)
    push_handler = SERVER_PANEL_JS[start:end]

    assert "GitToolsSemanticPanel?.interceptPushControl" in push_handler
    assert 'sourceControlId: "git-server-push-local"' in push_handler
    assert "parameters: payload" in push_handler
    assert "executePush: async" in push_handler
    assert "ensureGitServerDockerAvailable" in push_handler
    assert "runGitServerOperationRequest" in push_handler
    assert "gitToolsStatusApi().endpoints.serverPushLocal" in push_handler
    assert "executionContext.preflightReceiptId" in push_handler
    assert "executionContext.confirmationReceiptId" in push_handler
    assert "executeIntent" not in push_handler
    assert "force" not in push_handler.lower()


def test_existing_push_control_confirms_revalidates_executes_and_preserves_semantics() -> None:
    registry_path = WEB_APP / "scripts/mcel-domain-adapter-registry.js"
    adapter_path = WEB_APP / "scripts/git-tools-semantic-adapter.js"
    panel_path = WEB_APP / "scripts/git-tools-semantic-panel.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        const statusAhead = [1, 1, 0];
        let statusCalls = 0;
        sandbox.GitToolsStatusApi = {{
          async fetchStatus(options) {{
            const ahead = statusAhead[Math.min(statusCalls, statusAhead.length - 1)];
            statusCalls += 1;
            return {{
              ok: true,
              repo_dir: "C:/work/main_computer_test",
              git_root: "C:/work/main_computer_test",
              is_git_repo: true,
              has_head: true,
              branch: "main",
              ahead,
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
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(panel_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-panel.js"}}
        );

        (async () => {{
          function makeNode(id = "") {{
            return {{
              id,
              dataset: {{}},
              textContent: "",
              title: "",
              disabled: false,
              attributes: {{}},
              handlers: {{}},
              setAttribute(name, value) {{ this.attributes[name] = String(value); }},
              addEventListener(type, handler) {{ this.handlers[type] = handler; }}
            }};
          }}

          const selectors = [
            "#git-semantic-runtime-panel",
            "#git-semantic-runtime-status",
            "#git-semantic-state-freshness",
            "#git-semantic-repository",
            "#git-semantic-branch",
            "#git-semantic-divergence",
            "#git-semantic-push-decision",
            "#git-semantic-receipt-count",
            "#git-semantic-execution-enabled",
            "#git-semantic-runtime-core-ready",
            "#git-semantic-application-coverage",
            "#git-semantic-runtime-scope",
            "#git-semantic-intent-coverage-summary",
            "#git-semantic-intent-coverage-matrix",
            "#git-semantic-refresh-state",
            "#git-semantic-run-push-preflight",
            "#git-semantic-view-latest-receipt",
            "#git-semantic-clear-receipts",
            "#git-semantic-recovery-class",
            "#git-semantic-recovery-severity",
            "#git-semantic-recovery-retry-safe",
            "#git-semantic-recovery-refresh-required",
            "#git-semantic-recovery-next-step",
            "#git-semantic-recovery-prohibited",
            "#git-semantic-recovery-source-receipt",
            "#git-semantic-recovery-coverage",
            "#git-semantic-recovery-options",
            "#git-server-push-local",
            "#git-semantic-runtime-message",
            "#git-semantic-receipt-output"
          ];
          const nodes = Object.fromEntries(
            selectors.map((selector) => [selector, makeNode(selector.slice(1))])
          );

          let layoutState = {{
            supportView: "server",
            activeSurface: "workflow",
            supportOpen: true
          }};
          const selections = [];
          const layoutController = {{
            get resolved() {{ return {{...layoutState}}; }},
            selectSupport(view) {{
              selections.push(view);
              layoutState = {{
                supportView: view,
                activeSurface: "support",
                supportOpen: true
              }};
              return {{ok: true, resolved: {{...layoutState}}}};
            }}
          }};
          const root = {{
            querySelector(selector) {{ return nodes[selector] || null; }},
            __mcelGitToolsLayoutController: layoutController
          }};
          const documentObject = {{
            querySelector(selector) {{ return selector === "#git-tools-app" ? root : null; }}
          }};

          let confirmCalls = 0;
          let confirmationPrompt = "";
          let backendCalls = 0;
          let executionContext = null;
          const event = {{
            defaultPrevented: false,
            propagationStopped: false,
            preventDefault() {{ this.defaultPrevented = true; }},
            stopPropagation() {{ this.propagationStopped = true; }}
          }};
          const result = await sandbox.GitToolsSemanticPanel.interceptPushControl(event, {{
            document: documentObject,
            root,
            adapter: sandbox.GitToolsSemanticAdapter,
            registry: sandbox.McelDomainAdapterRegistry,
            sourceControl: nodes["#git-server-push-local"],
            parameters: {{
              repo_dir: "C:/work/main_computer_test",
              remote: "local-gitea",
              owner: "local",
              repo: "main-computer",
              protocol: "http",
              switch_origin: false
            }},
            confirm(prompt) {{
              confirmCalls += 1;
              confirmationPrompt = prompt;
              return true;
            }},
            async executePush(context) {{
              backendCalls += 1;
              executionContext = context;
              return {{
                ok: true,
                remote: "local-gitea",
                operation: {{id: "operation-panel-1", status: "succeeded"}}
              }};
            }}
          }});

          process.stdout.write(JSON.stringify({{
            result,
            statusCalls,
            confirmCalls,
            confirmationPrompt,
            backendCalls,
            executionContext,
            event: {{
              defaultPrevented: event.defaultPrevented,
              propagationStopped: event.propagationStopped
            }},
            layoutState,
            selections,
            sourcePush: {{
              disabled: nodes["#git-server-push-local"].disabled,
              gate: nodes["#git-server-push-local"].dataset.mcelSemanticGate,
              ariaDisabled: nodes["#git-server-push-local"].attributes["aria-disabled"],
              title: nodes["#git-server-push-local"].title
            }},
            message: nodes["#git-semantic-runtime-message"].textContent,
            output: nodes["#git-semantic-receipt-output"].textContent,
            executionEnabled: nodes["#git-semantic-execution-enabled"].textContent,
            runtimeScope: nodes["#git-semantic-runtime-scope"].textContent
          }}));
        }})().catch((error) => {{
          console.error(error && error.stack ? error.stack : error);
          process.exit(1);
        }});
        """
    )
    result = run_node_json(script)

    assert result["statusCalls"] == 3
    assert result["confirmCalls"] == 1
    assert result["backendCalls"] == 1
    assert "Push the current committed HEAD to Local Gitea?" in result["confirmationPrompt"]
    assert "Remote: local-gitea" in result["confirmationPrompt"]
    assert result["executionContext"]["parameters"]["remote"] == "local-gitea"
    assert result["result"]["status"] == "succeeded"
    assert result["result"]["executionAttempted"] is True
    assert result["result"]["sourceControlId"] == "git-server-push-local"
    assert result["result"]["receipt"]["result"]["operationId"] == "operation-panel-1"
    assert result["result"]["receipt"]["preflightReceiptId"]
    assert result["result"]["receipt"]["confirmationReceiptId"]
    assert result["event"] == {
        "defaultPrevented": True,
        "propagationStopped": True,
    }
    assert result["layoutState"] == {
        "supportView": "semantics",
        "activeSurface": "support",
        "supportOpen": True,
    }
    assert result["selections"]
    assert set(result["selections"]) == {"semantics"}
    assert result["sourcePush"]["disabled"] is False
    assert result["sourcePush"]["gate"] == "governed-execution"
    assert result["sourcePush"]["ariaDisabled"] == "false"
    assert "explicit confirmation" in result["sourcePush"]["title"]
    assert "push succeeded" in result["message"].lower()
    assert "Execution attempted: yes" in result["output"]
    assert "Preflight receipt:" in result["output"]
    assert "Confirmation receipt:" in result["output"]
    assert result["executionEnabled"] == "Refresh + governed push"
    assert result["runtimeScope"] == "Governed Publish Partial"


def test_existing_push_control_decline_never_invokes_backend() -> None:
    source_path = WEB_APP / "scripts/git-tools-semantic-panel.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(source_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-panel.js"}}
        );

        (async () => {{
          function makeNode(id = "") {{
            return {{
              id,
              dataset: {{}},
              textContent: "",
              title: "",
              disabled: false,
              attributes: {{}},
              handlers: {{}},
              setAttribute(name, value) {{ this.attributes[name] = String(value); }},
              addEventListener(type, handler) {{ this.handlers[type] = handler; }}
            }};
          }}
          const nodes = {{
            "#git-semantic-runtime-panel": makeNode("git-semantic-runtime-panel"),
            "#git-server-push-local": makeNode("git-server-push-local"),
            "#git-semantic-runtime-message": makeNode("git-semantic-runtime-message"),
            "#git-semantic-receipt-output": makeNode("git-semantic-receipt-output")
          }};
          const root = {{
            querySelector(selector) {{ return nodes[selector] || null; }},
            __mcelGitToolsLayoutController: {{
              resolved: {{
                supportView: "semantics",
                activeSurface: "support",
                supportOpen: true
              }},
              selectSupport() {{ return {{ok: true}}; }}
            }}
          }};
          const documentObject = {{
            querySelector(selector) {{ return selector === "#git-tools-app" ? root : null; }}
          }};
          const state = {{
            phase: "ready",
            observedAt: new Date().toISOString(),
            repoDir: "C:/repo",
            gitRoot: "C:/repo",
            branch: "main",
            ahead: 1,
            behind: 0,
            dirty: false
          }};
          const receipts = [];
          let backendCalls = 0;
          const adapter = {{
            getState() {{ return {{...state}}; }},
            listReceipts() {{ return receipts.slice(); }},
            getRecoveryCoverage() {{ return {{coverageReady: true}}; }},
            getIntentCoverage() {{
              return {{
                semanticRuntimeScope: "governed-publish-partial",
                counts: {{total: 7, executable: 2, preflightOnly: 1, declaredOnly: 3, prohibited: 1}},
                entries: []
              }};
            }},
            async executeIntent(intentId, options = {{}}) {{
              if (intentId === "refreshStatus") {{
                return {{status: "succeeded", stateAfter: {{...state}}, receipt: null}};
              }}
              const receipt = {{
                receiptId: "confirmation-declined-1",
                kind: "confirmation-decision-receipt",
                intentId,
                status: "cancelled",
                decision: "decline",
                createdAt: new Date().toISOString(),
                stateFingerprint: "fingerprint",
                stateContentFingerprint: "content-fingerprint",
                preflightReceiptId: "preflight-1",
                blockers: [{{code: "confirmation-declined", message: "declined"}}],
                warnings: [],
                executionAttempted: false,
                executionBinding: "git-tools-server-panel.serverPushLocal",
                result: {{status: "cancelled"}}
              }};
              receipts.push(receipt);
              if (options.executeBinding) {{
                // The adapter deliberately does not call this binding on decline.
              }}
              return {{
                status: "cancelled",
                decision: "decline",
                executionAttempted: false,
                confirmationReceipt: receipt,
                receipt
              }};
            }},
            preflightIntent(intentId, suppliedState, options) {{
              const receipt = {{
                receiptId: "preflight-1",
                kind: "preflight-decision-receipt",
                intentId,
                status: "confirmation-required",
                decision: "confirm",
                createdAt: new Date().toISOString(),
                stateFingerprint: "fingerprint",
                stateContentFingerprint: "content-fingerprint",
                parameters: options.parameters,
                blockers: [],
                warnings: [],
                executionAttempted: false
              }};
              receipts.push(receipt);
              return {{
                intentId,
                decision: "confirm",
                state: suppliedState,
                parameters: options.parameters,
                receipt
              }};
            }}
          }};
          const registry = {{
            evaluateAdapterReadiness() {{
              return {{
                runtimeCoreReady: true,
                fullApplicationSemanticReady: false,
                semanticRuntimeScope: "governed-publish-partial",
                executableIntentCount: 2,
                preflightOnlyIntentCount: 1,
                declaredOnlyIntentCount: 3,
                prohibitedIntentCount: 1,
                blockedIntentCount: 4,
                totalIntentCount: 7,
                adapterExecutable: true
              }};
            }}
          }};
          const result = await sandbox.GitToolsSemanticPanel.interceptPushControl(null, {{
            document: documentObject,
            root,
            adapter,
            registry,
            parameters: {{remote: "local-gitea"}},
            confirm() {{ return false; }},
            async executePush() {{
              backendCalls += 1;
              return {{ok: true}};
            }}
          }});
          process.stdout.write(JSON.stringify({{
            result,
            backendCalls,
            message: nodes["#git-semantic-runtime-message"].textContent,
            output: nodes["#git-semantic-receipt-output"].textContent
          }}));
        }})();
        """
    )
    result = run_node_json(script)
    assert result["backendCalls"] == 0
    assert result["result"]["status"] == "cancelled"
    assert result["result"]["executionAttempted"] is False
    assert "declined" in result["message"].lower()
    assert "Execution attempted: no" in result["output"]


def test_semantic_panel_renders_receipt_linked_recovery_guidance() -> None:
    registry_path = WEB_APP / "scripts/mcel-domain-adapter-registry.js"
    adapter_path = WEB_APP / "scripts/git-tools-semantic-adapter.js"
    panel_path = WEB_APP / "scripts/git-tools-semantic-panel.js"
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
          fs.readFileSync({json.dumps(str(panel_path))}, "utf8"),
          sandbox,
          {{filename: "git-tools-semantic-panel.js"}}
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
          observedAt: "2026-07-15T22:10:00.000Z"
        }});
        adapter.resetState({{repoDir: "C:/work/main_computer_test"}});
        const preflight = adapter.preflightIntent("pushCurrentBranch", state, {{
          now: "2026-07-15T22:10:30.000Z"
        }});
        const panel = sandbox.GitToolsSemanticPanel;
        const model = panel.buildViewModel({{
          adapter,
          registry: sandbox.McelDomainAdapterRegistry,
          now: Date.parse("2026-07-15T22:10:30.000Z")
        }});
        console.log(JSON.stringify({{
          model,
          receiptText: panel.receiptText(preflight.receipt)
        }}));
        """
    )
    result = run_node_json(script)
    model = result["model"]
    assert model["runtimeStatus"] == "Core ready · partial coverage"
    assert model["executionEnabled"] == "Refresh + governed push"
    coverage = model["intentCoverage"]
    assert coverage["runtimeCoreReady"] is True
    assert coverage["runtimeCoreLabel"] == "Ready"
    assert coverage["fullApplicationSemanticReady"] is False
    assert coverage["applicationCoverageLabel"] == "Partial"
    assert coverage["semanticRuntimeScope"] == "governed-publish-partial"
    assert coverage["semanticRuntimeScopeLabel"] == "Governed Publish Partial"
    assert coverage["executable"] == 2
    assert coverage["preflightOnly"] == 1
    assert coverage["declaredOnly"] == 3
    assert coverage["prohibited"] == 1
    assert coverage["blocked"] == 4
    assert coverage["total"] == 7
    assert coverage["summary"] == (
        "2 executable · 1 preflight only · 3 declared only · 1 prohibited"
    )
    assert [entry["status"] for entry in coverage["entries"]] == [
        "executable",
        "declared-only",
        "declared-only",
        "declared-only",
        "preflight-only",
        "executable",
        "prohibited",
    ]
    assert model["recovery"]["available"] is True
    assert model["recovery"]["failureClass"] == "nothing-to-publish"
    assert model["recovery"]["severity"] == "informational"
    assert model["recovery"]["retrySafe"] == "No"
    assert model["recovery"]["refreshRequired"] == "No"
    assert model["recovery"]["sourceReceipt"].startswith("git-tools-receipt-")
    assert model["recovery"]["coverageReady"] is True
    assert model["recovery"]["coverageStatus"] == "Verified coverage"
    assert model["recovery"]["optionLabels"] == [
        "Inspect working-tree changes",
        "Commit intended changes outside MCEL",
    ]
    assert model["recovery"]["prohibitedActions"] == "pushCurrentBranch"
    assert "Recovery classified: yes" in result["receiptText"]
    assert "Failure class: nothing-to-publish" in result["receiptText"]
    assert "Recommended next step: Inspect the working tree" in result["receiptText"]
    assert "Prohibited actions: pushCurrentBranch" in result["receiptText"]
