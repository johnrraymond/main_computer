"""Read-only lab projection of the canonical MCEL DSL application IR.

Never stages/promotes a candidate or changes the Calculator host markup.
"""
from __future__ import annotations

import json
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application


def project(repo_root: Path, *, app_id: str = "calculator") -> str:
    # Lab v2 permits one declared specimen; app_id is not a client-chosen path.
    if app_id != "calculator":
        raise ValueError("Unsupported lab presentation specimen")
    report = compile_dsl_application(repo_root / "mcel_apps" / app_id / "application.js")
    if not report.valid or not report.normalized_ir or report.app_id != app_id:
        raise ValueError(f"Lab semantic source unavailable: {report.diagnostics}")
    ir = report.normalized_ir
    surfaces = [surface for surface in ir["surfaces"] if surface.get("root") == "#calculator-app"]
    if len(surfaces) != 1:
        raise ValueError("Canonical Calculator surface identity is unresolved")
    surface = surfaces[0]
    semantic = surface.get("semanticSurface") or {}
    # The hostMarkup is already projected by the normal host compiler. Do not
    # copy it into the lab source, and do not invent labels, action classes, etc.
    projection = {
        "schema": ir["schema"],
        "application": {"appId": app_id},
        "fingerprints": ir.get("fingerprints", {}),
        "surfaces": [{"id": surface.get("id"), "root": surface.get("root"),
                      "nodes": surface.get("nodes", []),
                      "semanticSurface": {"controls": semantic.get("controls", []),
                                          "regions": semantic.get("regions", [])}}],
        "intents": ir.get("intents", []),
    }
    encoded = json.dumps(projection, ensure_ascii=True, separators=(",", ":"))
    encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return "window.McelLabCanonicalPresentationSources=Object.freeze({calculator:" + encoded + "});\n"
