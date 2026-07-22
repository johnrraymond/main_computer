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
DOC = ROOT / "pretty_docs" / "mcel-code-editor-surface-status.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; MCEL surface status widget smoke test cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_status_api_script(body: str) -> str:
    return textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        sandbox.setInterval = () => 1;
        sandbox.clearInterval = () => undefined;
        vm.runInNewContext(fs.readFileSync({json.dumps(str(STATUS_SCRIPT))}, "utf8"), sandbox, {{filename: "mcel-code-editor-surface-status.js"}});
        const api = sandbox.McelCodeEditorSurfaceStatus;
        {body}
        """
    )


def test_code_editor_contains_visible_mcel_surface_status_chip() -> None:
    app = APP_PATH.read_text(encoding="utf-8")
    style = STYLE_PATH.read_text(encoding="utf-8")
    applications = APPLICATIONS_HTML.read_text(encoding="utf-8")
    source = STATUS_SCRIPT.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")

    assert 'id="code-editor-mcel-surface-status"' in app
    assert 'data-mcel-surface-status="code-editor"' in app
    assert 'data-mcel-surface-status-state="pending"' in app
    assert 'data-mcel-surface-id="code-editor.surface.mcel-status-chip"' in app
    assert "MCEL Surface" in app

    assert ".code-editor-mcel-surface-status" in style
    assert 'data-mcel-surface-status-state="pass"' in style
    assert 'data-mcel-surface-status-state="fail"' in style

    assert "applications/scripts/mcel-code-editor-surface-status.js" in applications
    assert applications.index("mcel-self-diagnosis.js") < applications.index("mcel-code-editor-surface-status.js")

    assert "mcel-code-editor-surface-status-v1" in source
    assert "McelCodeEditorSurfaceStatus" in source
    assert "summarizePathway" in source
    assert "renderStatus" in source

    assert "SemanticSurfaceIR" in doc
    assert "SharedLayoutGrammar" in doc


def test_surface_status_summarizes_passing_pathway() -> None:
    payload = run_node_json(load_status_api_script(
        """
        const summary = api.summarizePathway({
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
        });
        process.stdout.write(JSON.stringify(summary));
        """
    ))

    assert payload["state"] == "pass"
    assert payload["value"] == "PASS"
    assert payload["details"] == {
        "semanticRidges": "pass",
        "surfaceIR": "pass",
        "layout": "pass",
        "extraction": "pass",
        "roundTrip": "pass",
    }
    assert "Round-trip PASS" in payload["title"]


def test_surface_status_summarizes_failed_pathway_predicate() -> None:
    payload = run_node_json(load_status_api_script(
        """
        const summary = api.summarizePathway({
          status: "fail",
          valid: false,
          semanticRidgesPresent: true,
          surfaceIrBuildable: true,
          surfaceIrValid: true,
          layoutGrammarPresent: true,
          layoutGrammarValid: false,
          extractable: true,
          roundTripStatus: "fail",
          counts: {errors: 1, warnings: 0, ok: 4}
        });
        process.stdout.write(JSON.stringify(summary));
        """
    ))

    assert payload["state"] == "fail"
    assert payload["value"] == "FAIL"
    assert payload["details"]["semanticRidges"] == "pass"
    assert payload["details"]["layout"] == "fail"
    assert payload["details"]["roundTrip"] == "fail"


def test_surface_status_render_updates_visible_chip_markup() -> None:
    payload = run_node_json(load_status_api_script(
        """
        const element = {
          dataset: {},
          attrs: {},
          innerHTML: "",
          setAttribute(name, value) { this.attrs[name] = String(value); }
        };
        const summary = api.renderStatus(element, api.summarizePathway({
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
        }));
        process.stdout.write(JSON.stringify({
          summary,
          dataset: element.dataset,
          attrs: element.attrs,
          innerHTML: element.innerHTML
        }));
        """
    ))

    assert payload["summary"]["state"] == "pass"
    assert payload["dataset"]["mcelSurfaceStatusState"] == "pass"
    assert payload["attrs"]["data-mcel-surface-status-state"] == "pass"
    assert "MCEL Surface" in payload["innerHTML"]
    assert "PASS" in payload["innerHTML"]
    assert "Round-trip PASS" in payload["attrs"]["title"]
