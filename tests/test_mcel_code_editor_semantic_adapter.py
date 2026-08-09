from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from main_computer.mcel_node_runtime import resolve_node_executable


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
ADAPTER = SCRIPTS / "code-editor-semantic-adapter.js"
RUNTIME = SCRIPTS / "code-editor.js"
SHELL = ROOT / "main_computer" / "web" / "applications.html"


def test_code_editor_semantic_adapter_is_retired_from_the_host_shell() -> None:
    shell = SHELL.read_text(encoding="utf-8")
    adapter_source = ADAPTER.read_text(encoding="utf-8")

    assert "applications/scripts/code-editor-semantic-adapter.js" not in shell
    assert "retired: true" in adapter_source
    assert 'authority: "MainComputerCodeEditorRuntime"' in adapter_source
    assert "McelDomainAdapterRegistry" not in adapter_source
    assert "registerAdapter" not in adapter_source


def test_code_editor_retired_adapter_exports_only_a_legacy_marker() -> None:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; Code Editor retired-adapter tests cannot run")

    script = f"""
const adapter = require({json.dumps(str(ADAPTER))});
console.log(JSON.stringify(adapter));
"""
    completed = subprocess.run([node, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload == {
        "schema": "main-computer-retired-code-editor-semantic-adapter-v1",
        "appId": "code-editor",
        "retired": True,
        "authority": "MainComputerCodeEditorRuntime",
        "reason": "Code Editor is now authored by the MCEL DSL package and implemented by the canonical runtime facade.",
        "runtimeFacade": "MainComputerCodeEditorRuntime",
    }


def test_canonical_runtime_contains_the_reviewed_patch_methods_instead() -> None:
    runtime_source = RUNTIME.read_text(encoding="utf-8")

    assert "async function previewAiderPlan" in runtime_source
    assert "async function applyReviewedPatch" in runtime_source
    assert "capabilities.previewAiderPlan" in runtime_source
    assert "capabilities.applyReviewedPatch" in runtime_source
    assert "patch-lane-not-migrated" not in runtime_source


def test_code_editor_runtime_does_not_reference_old_studio_authority() -> None:
    runtime_source = RUNTIME.read_text(encoding="utf-8")
    assert "MainComputerCodeStudio" not in runtime_source
    assert "MainComputerCodeEditorRuntime" in runtime_source
