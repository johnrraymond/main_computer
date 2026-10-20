"""Code Editor host projection from the canonical compiled MCEL application."""
from __future__ import annotations
from pathlib import Path
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_onlyoffice_host_projection import render

REQUIRED = ('id="code-editor-app"', 'id="code-editor-diagnostics-counter"',
            'id="code-studio-save-live-workspace"', 'id="code-studio-bottom-panel"',
            'id="code-studio-commit-runtime"')

def project(repo_root: Path) -> str:
    compiled = compile_dsl_application(repo_root / "mcel_apps/code-editor/application.js")
    if not compiled.valid or compiled.normalized_ir is None or compiled.app_id != "code-editor":
        raise ValueError(f"Code Editor MCEL compilation failed: {compiled.diagnostics}")
    surfaces = [s for s in compiled.normalized_ir["surfaces"] if s.get("root") == "#code-editor-app"]
    if len(surfaces) != 1:
        raise ValueError("Code Editor must declare exactly one root surface")
    markup = (surfaces[0].get("semanticSurface") or {}).get("hostMarkup")
    if not isinstance(markup, dict) or markup.get("attrs", {}).get("id") != "code-editor-app":
        raise ValueError("Compiled Code Editor host markup is missing")
    output = render(markup).strip() + "\n"
    for marker in REQUIRED:
        if marker not in output:
            raise ValueError(f"Missing Code Editor control: {marker}")
    return output
