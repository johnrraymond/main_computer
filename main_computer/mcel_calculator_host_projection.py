"""Render Calculator host markup from its canonical compiled MCEL app."""
from __future__ import annotations
from pathlib import Path
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_onlyoffice_host_projection import render

REQUIRED = ('id="calculator-app"', 'id="calculator-mode-basic"',
            'id="calculator-mode-graphing"', 'id="calculator-graphing-panel"',
            'id="calculator-mathics-panel"', 'id="calculator-qa-panel"')

def project(repo_root: Path) -> str:
    source = repo_root / "mcel_apps/calculator/application.js"
    compiled = compile_dsl_application(source)
    if not compiled.valid or compiled.normalized_ir is None or compiled.app_id != "calculator":
        raise ValueError(f"Calculator native MCEL compilation failed: {compiled.diagnostics}")
    surfaces = [s for s in compiled.normalized_ir["surfaces"] if s.get("root") == "#calculator-app"]
    if len(surfaces) != 1:
        raise ValueError("Calculator must declare exactly one host surface")
    markup = (surfaces[0].get("semanticSurface") or {}).get("hostMarkup")
    if not isinstance(markup, dict) or markup.get("attrs", {}).get("id") != "calculator-app":
        raise ValueError("Compiled Calculator host markup is missing")
    def normalize(node):
        if "text" in node:
            return node
        return {**node, "attrs": {k: ("" if v is None else v) for k, v in node.get("attrs", {}).items()},
                "children": [normalize(child) for child in node.get("children", [])]}
    output = render(normalize(markup)).strip() + "\n"
    for marker in REQUIRED:
        if marker not in output:
            raise ValueError(f"Calculator control missing: {marker}")
    return output
