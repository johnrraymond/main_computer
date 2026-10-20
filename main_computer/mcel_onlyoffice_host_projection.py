"""Render the ONLYOFFICE host from native compiled MCEL DSL IR."""
from __future__ import annotations
import html
from pathlib import Path

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

def render(node):
    if "text" in node:
        return html.escape(node["text"], quote=False)
    tag = node["tag"]
    if not tag.replace("-", "").isalnum():
        raise ValueError(f"Invalid tag: {tag!r}")
    attrs = "".join(
        " " + key + "=\"" + html.escape(str(value), quote=True) + "\""
        for key, value in node.get("attrs", {}).items()
    )
    opening = "<" + tag + attrs + ">"
    if tag in VOID:
        if node.get("children"):
            raise ValueError(f"Void node {tag!r} has children")
        return opening
    return opening + "".join(render(child) for child in node.get("children", [])) + "</" + tag + ">"

def project(repo_root: Path):
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    source = repo_root / "main_computer/web/applications/layouts/onlyoffice.application.js"
    compiled = compile_dsl_application(source)
    if not compiled.valid or compiled.normalized_ir is None:
        raise ValueError(f"ONLYOFFICE native MCEL DSL compilation failed: {compiled.diagnostics}")
    ir = compiled.normalized_ir
    if compiled.app_id != "onlyoffice":
        raise ValueError(f"Compiled wrong MCEL app: {compiled.app_id!r}")
    surfaces = [surface for surface in ir["surfaces"] if surface.get("root") == "#onlyoffice-app"]
    if len(surfaces) != 1:
        raise ValueError("Native MCEL DSL must declare exactly one ONLYOFFICE host surface")
    semantic = surfaces[0].get("semanticSurface") or {}
    root = semantic.get("hostMarkup")
    if not isinstance(root, dict) or root.get("attrs", {}).get("id") != "onlyoffice-app":
        raise ValueError("Compiled MCEL surface has no valid ONLYOFFICE host markup")
    output = render(root).strip() + "\n"
    required = (
        'id="onlyoffice-app"', 'id="onlyoffice-file-list"',
        'id="onlyoffice-editor-host"', 'id="onlyoffice-diagnostics-counter"',
        'id="onlyoffice-save-now"', 'id="onlyoffice-reload-editor"',
        'id="onlyoffice-new-workbook"', 'id="onlyoffice-upload-file"',
    )
    if any(marker not in output for marker in required):
        raise ValueError("MCEL layout definition is missing a required ONLYOFFICE binding")
    return output

