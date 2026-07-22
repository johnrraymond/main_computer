from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "main_computer" / "web" / "applications" / "apps" / "code-editor.html"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
STYLE_PATH = ROOT / "main_computer" / "web" / "applications" / "styles" / "code-editor.css"
STATUS_SCRIPT = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-code-editor-surface-status.js"
INSPECTOR_SCRIPT = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-code-editor-ridge-inspector.js"
DOC = ROOT / "pretty_docs" / "mcel-code-editor-ridge-inspector.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; MCEL ridge inspector smoke test cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_inspector_api_script(body: str) -> str:
    return textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(fs.readFileSync({json.dumps(str(STATUS_SCRIPT))}, "utf8"), sandbox, {{filename: "mcel-code-editor-surface-status.js"}});
        vm.runInNewContext(fs.readFileSync({json.dumps(str(INSPECTOR_SCRIPT))}, "utf8"), sandbox, {{filename: "mcel-code-editor-ridge-inspector.js"}});
        const api = sandbox.McelCodeEditorRidgeInspector;
        {body}
        """
    )


def test_code_editor_status_chip_is_ridge_inspector_trigger() -> None:
    app = APP_PATH.read_text(encoding="utf-8")
    applications = APPLICATIONS_HTML.read_text(encoding="utf-8")
    style = STYLE_PATH.read_text(encoding="utf-8")
    source = INSPECTOR_SCRIPT.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")

    assert 'id="code-editor-mcel-surface-status"' in app
    assert 'data-mcel-ridge-inspector-trigger="code-editor"' in app
    assert 'aria-haspopup="dialog"' in app
    assert 'aria-expanded="false"' in app
    assert 'aria-controls="code-editor-mcel-ridge-inspector"' in app
    assert 'tabindex="0"' in app

    assert "applications/scripts/mcel-code-editor-ridge-inspector.js" in applications
    assert applications.index("mcel-code-editor-surface-status.js") < applications.index("mcel-code-editor-ridge-inspector.js")

    assert ".code-editor-mcel-ridge-inspector" in style
    assert ".code-editor-mcel-ridge-inspector[hidden]" in style
    assert "position: absolute" in style
    assert "max-height" in style

    assert "mcel-code-editor-ridge-inspector-v1" in source
    assert "McelCodeEditorRidgeInspector" in source
    assert "buildInspectorModel" in source
    assert "renderInspectorContent" in source
    assert "toggleInspector" in source

    assert "hidden by default" in doc
    assert "supporting-diagnostic-surface" in doc


def test_ridge_inspector_builds_pass_model_from_surface_pathway() -> None:
    payload = run_node_json(load_inspector_api_script(
        """
        const model = api.buildInspectorModel({
          report: {
            primarySurface: {
              expected: "code-editor.surface.monaco-selected-file-editor",
              host: {selector: "#code-studio-runtime-monaco"},
              editor: {selector: ".monaco-editor"},
              ownership: {
                primarySurfaceId: "code-editor.surface.monaco-selected-file-editor",
                hostSelector: "#code-studio-runtime-monaco",
                editorSelector: ".monaco-editor"
              }
            },
            mcelSurfacePathway: {
              status: "pass",
              valid: true,
              semanticRidgesPresent: true,
              surfaceIrBuildable: true,
              surfaceIrValid: true,
              layoutGrammarPresent: true,
              layoutGrammarValid: true,
              extractable: true,
              roundTripStatus: "pass",
              counts: {errors: 0, warnings: 0, ok: 6}
            }
          }
        });
        process.stdout.write(JSON.stringify(model));
        """
    ))

    assert payload["version"] == "mcel-code-editor-ridge-inspector-v1"
    assert payload["state"] == "pass"
    assert payload["value"] == "PASS"
    assert payload["surfaceId"] == "code-editor.surface.monaco-selected-file-editor"
    assert payload["hostSelector"] == "#code-studio-runtime-monaco"
    assert payload["editorSelector"] == ".monaco-editor"
    assert [check["state"] for check in payload["checks"]] == ["pass", "pass", "pass", "pass", "pass"]


def test_ridge_inspector_renders_bounded_popover_content() -> None:
    payload = run_node_json(load_inspector_api_script(
        """
        const html = api.renderInspectorContent(api.buildInspectorModel({
          report: {
            primarySurface: {expected: "code-editor.surface.monaco-selected-file-editor"},
            mcelSurfacePathway: {
              status: "pass",
              valid: true,
              semanticRidgesPresent: true,
              surfaceIrBuildable: true,
              surfaceIrValid: true,
              layoutGrammarPresent: true,
              layoutGrammarValid: true,
              extractable: true,
              roundTripStatus: "pass",
              counts: {errors: 0, warnings: 0, ok: 6}
            }
          }
        }));
        process.stdout.write(JSON.stringify({html}));
        """
    ))

    html = payload["html"]
    assert "MCEL ridge inspector" in html
    assert "Semantic ridges" in html
    assert "SemanticSurfaceIR" in html
    assert "Shared layout grammar" in html
    assert "Surface extraction" in html
    assert "Round-trip verification" in html
    assert 'data-mcel-ridge-check-state="pass"' in html
    assert "No active MCEL surface-pathway issues." in html


def test_ridge_inspector_escapes_report_text() -> None:
    payload = run_node_json(load_inspector_api_script(
        """
        const html = api.renderInspectorContent(api.buildInspectorModel({
          report: {
            primarySurface: {
              expected: "<bad-surface>",
              host: {selector: "<host>"},
              editor: {selector: "<editor>"}
            },
            mcelSurfacePathway: {
              status: "fail",
              valid: false,
              semanticRidgesPresent: true,
              surfaceIrBuildable: true,
              surfaceIrValid: true,
              layoutGrammarPresent: false,
              layoutGrammarValid: false,
              extractable: false,
              roundTripStatus: "fail"
            }
          }
        }));
        process.stdout.write(JSON.stringify({html}));
        """
    ))

    html = payload["html"]
    assert "<bad-surface>" not in html
    assert "&lt;bad-surface&gt;" in html
    assert "&lt;host&gt;" in html
    assert "&lt;editor&gt;" in html
    assert 'data-mcel-ridge-check-state="fail"' in html
