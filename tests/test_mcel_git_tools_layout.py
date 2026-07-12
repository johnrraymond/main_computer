from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS_HTML = (PROJECT_ROOT / "main_computer/web/applications.html").read_text(encoding="utf-8")
GIT_TOOLS_HTML = (
    PROJECT_ROOT / "main_computer/web/applications/apps/git-tools.html"
).read_text(encoding="utf-8")
GIT_TOOLS_CSS = (
    PROJECT_ROOT / "main_computer/web/applications/styles/git-tools.css"
).read_text(encoding="utf-8")
GIT_TOOLS_JS = (
    PROJECT_ROOT / "main_computer/web/applications/scripts/git-tools-layout-contract.js"
).read_text(encoding="utf-8")
GIT_TOOLS_ENTRYPOINT_JS = (
    PROJECT_ROOT / "main_computer/web/applications/scripts/git-tools.js"
).read_text(encoding="utf-8")


def run_node_json(script: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_git_tools_live_contract_is_included_after_entrypoint() -> None:
    entrypoint = "<!-- @include applications/scripts/git-tools.js -->"
    contract = "<!-- @include applications/scripts/git-tools-layout-contract.js -->"
    assert entrypoint in APPLICATIONS_HTML
    assert contract in APPLICATIONS_HTML
    assert APPLICATIONS_HTML.index(entrypoint) < APPLICATIONS_HTML.index(contract)
    assert "global.MainComputerGitToolsLayout?.mount?" in GIT_TOOLS_ENTRYPOINT_JS


def test_git_tools_authors_semantic_workbench_units_and_chrome() -> None:
    expected = (
        'data-mc-layout-root="git-tools-application"',
        'data-mc-layout-policy="dominant-workflow-stack"',
        'data-mc-layout-user-id="repository.project-identity"',
        'data-mc-layout-user-id="repository.command"',
        'data-mc-layout-user-id="repository.command-workflow"',
        'data-mc-layout-user-id="repository.phase-support"',
        'data-mc-layout-user-id="repository.persistent-feedback"',
        'id="git-tools-command-surface"',
        'id="git-tools-support-tabs"',
        'id="git-tools-feedback-band"',
        'data-git-support-panel="server"',
        'data-git-support-panel="evidence"',
        'data-git-support-panel="advanced"',
        'data-git-layout-dock="repository.phase-support"',
        'data-git-layout-share="repository.phase-support"',
    )
    for snippet in expected:
        assert snippet in GIT_TOOLS_HTML

    forbidden = (
        'data-mc-layout-x=',
        'data-mc-layout-y=',
        'data-mc-layout-w=',
        'data-mc-layout-h=',
    )
    for snippet in forbidden:
        assert snippet not in GIT_TOOLS_HTML

    assert 'root.addEventListener("click"' in GIT_TOOLS_JS
    assert 'root.addEventListener("change"' in GIT_TOOLS_JS
    assert 'kind: "dock"' in GIT_TOOLS_JS
    assert 'kind: "resize-share"' in GIT_TOOLS_JS
    assert 'kind: "select-surface"' in GIT_TOOLS_JS
    assert 'kind: "select-support"' in GIT_TOOLS_JS


def test_git_tools_css_realizes_owned_dock_tracks_and_responsive_presentations() -> None:
    expected = (
        '#git-tools-app[data-git-layout-live="true"] .git-tools-shell',
        '"workspace support"',
        'grid-template-areas: "identity workflow";',
        'var(--git-layout-support-inline)',
        'var(--git-layout-support-block)',
        '[data-git-layout-support="bottom"]',
        '[data-git-layout-support="tab"]',
        '[data-git-layout-support="stage"]',
        '[data-git-layout-support="trigger"]',
        '[data-git-layout-identity="top"]',
        '[data-git-layout-identity="trigger"]',
        'grid-template-rows: minmax(0, 1fr);',
        'overscroll-behavior: contain;',
        '[data-git-layout-support-view="advanced"]',
    )
    for snippet in expected:
        assert snippet in GIT_TOOLS_CSS


def test_git_tools_resolver_is_monotonic_and_rejects_raw_geometry() -> None:
    source_path = PROJECT_ROOT / "main_computer/web/applications/scripts/git-tools-layout-contract.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({json.dumps(str(source_path))}, "utf8");
        const sandbox = {{console}};
        sandbox.globalThis = sandbox;
        vm.runInNewContext(source, sandbox, {{filename: "git-tools-layout-contract.js"}});
        const api = sandbox.MainComputerGitToolsLayout;
        const authored = {{
          complete: true,
          missing: [],
          mismatches: [],
          units: api.SAFE_DEFAULTS,
        }};
        const preferences = api.normalizePreferences(api.DEFAULT_PREFERENCES, authored);
        const widths = [1600, 1200, 900, 680];
        const layouts = widths.map((width) => api.resolveLayout({{
          viewport: {{width, height: 900}},
          authored,
          preferences,
          phase: "selected-project-default",
        }}));
        const raw = api.normalizePreferences({{
          units: {{
            "repository.phase-support": {{
              placement: "right",
              x: 4,
              y: 2,
              width: 380,
            }},
          }},
        }}, authored);
        const docked = api.applyOperationToPreferences(
          preferences,
          {{kind: "dock", userId: "repository.phase-support", placement: "bottom"}},
          authored,
        );
        process.stdout.write(JSON.stringify({{
          capacities: layouts.map((item) => item.capacity),
          support: layouts.map((item) => item.actual.support),
          identity: layouts.map((item) => item.actual.identity),
          levels: layouts.map((item) => item.remediationLevel),
          rawGeometryViolations: raw.rawGeometryViolations,
          dockedPlacement: docked.preferences.units["repository.phase-support"].placement,
          contractVersion: api.CONTRACT_VERSION,
        }}));
        """
    )
    result = run_node_json(script)
    assert result["contractVersion"] == "mcel-git-tools-layout.v1"
    assert result["capacities"] == ["wide", "medium", "narrow", "compact"]
    assert result["support"] == ["right", "bottom", "tab", "stage"]
    assert result["identity"] == ["left", "left", "trigger", "trigger"]
    assert result["levels"] == [0, 1, 2, 3]
    assert result["dockedPlacement"] == "bottom"
    assert any(path.endswith(".x") for path in result["rawGeometryViolations"])
    assert any(path.endswith(".y") for path in result["rawGeometryViolations"])
    assert any(path.endswith(".width") for path in result["rawGeometryViolations"])


def test_git_tools_required_center_workflow_cannot_be_docked_or_collapsed() -> None:
    source_path = PROJECT_ROOT / "main_computer/web/applications/scripts/git-tools-layout-contract.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({json.dumps(str(source_path))}, "utf8");
        const sandbox = {{console}};
        sandbox.globalThis = sandbox;
        vm.runInNewContext(source, sandbox);
        const api = sandbox.MainComputerGitToolsLayout;
        const authored = {{
          complete: true,
          missing: [],
          mismatches: [],
          units: api.SAFE_DEFAULTS,
        }};
        const preferences = api.normalizePreferences(api.DEFAULT_PREFERENCES, authored);
        const dock = api.applyOperationToPreferences(
          preferences,
          {{kind: "dock", userId: "repository.command-workflow", placement: "right"}},
          authored,
        );
        const collapse = api.applyOperationToPreferences(
          preferences,
          {{kind: "collapse", userId: "repository.command-workflow", collapsed: true}},
          authored,
        );
        process.stdout.write(JSON.stringify({{
          dockOk: dock.ok,
          dockReason: dock.reason,
          collapseOk: collapse.ok,
          collapseReason: collapse.reason,
        }}));
        """
    )
    result = run_node_json(script)
    assert result["dockOk"] is False
    assert "does not permit placement" in result["dockReason"]
    assert result["collapseOk"] is False
    assert "does not permit collapsed" in result["collapseReason"]


def test_git_tools_structural_surfaces_neutralize_generic_widget_chrome() -> None:
    assert 'git-tools-project-card app-widget' in GIT_TOOLS_HTML
    assert 'git-server-pane app-widget' in GIT_TOOLS_HTML
    assert 'id="git-project-roster-surface" tabindex="-1"' in GIT_TOOLS_HTML
    assert 'id="git-project-workflow-surface" tabindex="-1"' in GIT_TOOLS_HTML
    assert 'id="git-workflow-accordion" tabindex="-1"' in GIT_TOOLS_HTML
    assert '.fullscreen-control,' in GIT_TOOLS_CSS
    assert '.widget-ticker {' in GIT_TOOLS_CSS
    assert 'display: none !important;' in GIT_TOOLS_CSS
    assert 'padding-top: 0 !important;' in GIT_TOOLS_CSS


def test_git_tools_surface_and_support_intents_resolve_to_visible_targets() -> None:
    source_path = PROJECT_ROOT / "main_computer/web/applications/scripts/git-tools-layout-contract.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const source = fs.readFileSync({json.dumps(str(source_path))}, "utf8");
        const sandbox = {{console}};
        sandbox.globalThis = sandbox;
        vm.runInNewContext(source, sandbox, {{filename: "git-tools-layout-contract.js"}});
        const api = sandbox.MainComputerGitToolsLayout;
        const authored = {{
          complete: true,
          missing: [],
          mismatches: [],
          units: api.SAFE_DEFAULTS,
        }};
        const preferences = api.normalizePreferences(api.DEFAULT_PREFERENCES, authored);
        const project = api.resolveLayout({{
          viewport: {{width: 1600, height: 900}},
          authored,
          preferences,
          phase: "selected-project-default",
          activeSurface: "identity",
          identityOpen: true,
        }});
        const evidence = api.resolveLayout({{
          viewport: {{width: 900, height: 900}},
          authored,
          preferences,
          phase: "proof-review",
          supportView: "evidence",
          activeSurface: "support",
          supportOpen: true,
        }});
        const recovery = api.resolveLayout({{
          viewport: {{width: 680, height: 900}},
          authored,
          preferences,
          phase: "recovery",
          supportView: "advanced",
          activeSurface: "support",
          supportOpen: true,
        }});
        process.stdout.write(JSON.stringify({{
          project: {{
            activeSurface: project.activeSurface,
            identity: project.actual.identity,
          }},
          evidence: {{
            activeSurface: evidence.activeSurface,
            supportView: evidence.supportView,
            support: evidence.actual.support,
            centerTab: evidence.centerTab,
          }},
          recovery: {{
            activeSurface: recovery.activeSurface,
            supportView: recovery.supportView,
            support: recovery.actual.support,
            stage: recovery.stage,
          }},
        }}));
        """
    )
    result = run_node_json(script)
    assert result["project"] == {
        "activeSurface": "identity",
        "identity": "left",
    }
    assert result["evidence"] == {
        "activeSurface": "support",
        "supportView": "evidence",
        "support": "tab",
        "centerTab": "support",
    }
    assert result["recovery"] == {
        "activeSurface": "support",
        "supportView": "advanced",
        "support": "stage",
        "stage": "support",
    }


def test_git_tools_delegated_layout_controls_cover_all_six_buttons() -> None:
    expected_buttons = (
        'data-git-layout-center="identity"',
        'data-git-layout-center="workflow"',
        'data-git-layout-center="support"',
        'data-git-layout-support="server"',
        'data-git-layout-support="evidence"',
        'data-git-layout-support="advanced"',
    )
    for snippet in expected_buttons:
        assert snippet in GIT_TOOLS_HTML
    assert '"[data-git-layout-center], [data-git-layout-support], [data-git-layout-action]"' in GIT_TOOLS_JS
    assert 'control.dataset.gitLayoutCenter' in GIT_TOOLS_JS
    assert 'control.dataset.gitLayoutSupport' in GIT_TOOLS_JS
    assert 'focusSurface(next)' in GIT_TOOLS_JS

def test_git_tools_preserves_publish_surface_across_support_views() -> None:
    assert 'id="git-server-publish-panel" tabindex="-1"' in GIT_TOOLS_HTML
    assert 'bindGitToolsControl(gitServerPushLocal, "click", pushLocalGitServerRemote);' in GIT_TOOLS_ENTRYPOINT_JS

    assert 'if (phase === "execution") state.supportView = "evidence";' not in GIT_TOOLS_JS
    assert 'else if (phase === "recovery") state.supportView = "advanced";' not in GIT_TOOLS_JS
    assert 'else if (phase === "planning") state.supportView = "server";' not in GIT_TOOLS_JS
    assert 'focusSupportView(state.supportView);' in GIT_TOOLS_JS
    assert 'server: "#git-server-publish-panel"' in GIT_TOOLS_JS

    assert '[data-git-support-panel]:not([data-git-support-panel="server"])' not in GIT_TOOLS_CSS
    assert '[data-git-support-panel="server"] {' in GIT_TOOLS_CSS
    assert 'selecting either adds that view without hiding the configured push path' in GIT_TOOLS_CSS

