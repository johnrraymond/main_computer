"""Materialize a genuinely host-HTML-free, fail-closed MCEL presentation probe.

This is *not* semantic-runtime certification.  The current Calculator/Code Editor
IR delegates all intents to external host facades.  The probe makes this gap
executable and refuses to mislabel layout-only evidence as behavioral parity.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_packages import build_application_package_catalog, repository_root
from main_computer.mcel_application_runtime_projection import build_application_runtime_projection
from main_computer.mcel_dsl_compiler import compile_dsl_application

SCHEMA = "mcel.standalone-package-presentation.v1"
_SIMPLE_SELECTOR = re.compile(r"^[#.][A-Za-z_][\w-]*$")
_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MCEL independent package presentation</title><link rel="stylesheet" href="./standalone.css"></head>
<body><main id="mcel-standalone-host"><p data-mcel-standalone-status>Loading independent package…</p><div data-mcel-standalone-content></div></main>
<script type="module" src="./standalone.js"></script></body></html>
"""
_CSS = """*{box-sizing:border-box}html,body{margin:0;width:100%;min-width:0}
body{font-family:system-ui,sans-serif;background:#f2f4f7;color:#17202a}
#mcel-standalone-host{width:100%;padding:12px;min-width:0}
[data-mcel-standalone-content] {min-width:0}
[data-mcel-structural-node]{min-width:0;max-width:100%;padding:8px;gap:8px}
[data-mcel-structural-node]:not([data-mcel-structural-root]){outline:1px solid #aeb7c0;border-radius:4px;background:#ffffff}
[data-mcel-structural-title]{font-size:12px;font-weight:600;white-space:normal}
[data-mcel-standalone-controls]{margin-top:16px;display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px}
[data-mcel-standalone-controls] button {min-height:44px;white-space:normal}
[data-mcel-standalone-status]{white-space:pre-wrap}
"""


class StandaloneProjectionError(ValueError):
    pass


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _check_node(node: Any, seen: set[str], depth: int = 0) -> None:
    if not isinstance(node, Mapping) or depth > 24:
        raise StandaloneProjectionError("Missing or excessively deep declarative layout node")
    identifier, selector = node.get("id"), node.get("selector")
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]*", identifier):
        raise StandaloneProjectionError("Invalid layout node ID")
    if identifier in seen:
        raise StandaloneProjectionError(f"Duplicate layout node ID: {identifier}")
    seen.add(identifier)
    if not isinstance(selector, str) or not _SIMPLE_SELECTOR.fullmatch(selector):
        raise StandaloneProjectionError(f"Standalone presentation cannot construct selector: {selector!r}")
    children = node.get("children", [])
    if not isinstance(children, list):
        raise StandaloneProjectionError("Layout children must be an ordered list")
    for variant in node.get("variants", []):
        if not isinstance(variant, Mapping):
            raise StandaloneProjectionError("Invalid layout variant")
        tracks, areas = variant.get("tracks"), variant.get("areas")
        if not isinstance(tracks, list) or not tracks or not isinstance(areas, list) or not areas:
            raise StandaloneProjectionError("Layout variant requires declared tracks and areas")
        if not all(isinstance(x, str) and x.strip() for x in tracks + areas):
            raise StandaloneProjectionError("Layout variant track or area is empty")
        referenced = set(" ".join(areas).split()) - {"."}
        if not referenced.issubset({c.get("id") for c in children if isinstance(c, Mapping)}):
            raise StandaloneProjectionError("Layout variant references an undeclared child")
    for child in children:
        _check_node(child, seen, depth+1)


def build_standalone_package(repo_root: Path, app_id: str, output_root: Path, *, portable: bool = False) -> Mapping[str, Any]:
    """Generate only from compiled DSL and fingerprinted generated contracts.

    Never load, copy, or reference either existing host HTML or host CSS.
    Never claim the runtimeMethod declarations implement portable effects.
    """
    repo = Path(repo_root).resolve()
    catalog = build_application_package_catalog(repo)
    if not catalog.ok or catalog.invalid_count:
        raise StandaloneProjectionError("The current package catalog is invalid")
    records = [r for r in catalog.packages if r.app_id == app_id and r.valid]
    if len(records) != 1:
        raise StandaloneProjectionError(f"No uniquely valid MCEL package: {app_id}")
    record = records[0]
    source = repo / record.package_root / "application.js"
    if not source.is_file():
        raise StandaloneProjectionError(f"No declarative source: {app_id}")
    compiled = compile_dsl_application(source, write_candidate=False)
    if not compiled.valid or not compiled.normalized_ir:
        raise StandaloneProjectionError(f"Source compilation refused: {app_id}")
    ir = compiled.normalized_ir
    if len(ir.get("layouts", [])) != 1 or len(ir.get("surfaces", [])) != 1:
        raise StandaloneProjectionError("One canonical layout and surface are required")
    layout = ir["layouts"][0].get("layoutTree")
    if not isinstance(layout, Mapping) or layout.get("schema") != "mcel.layout-tree.v1":
        raise StandaloneProjectionError("Canonical layout tree is missing")
    seen: set[str] = set()
    _check_node(layout.get("root"), seen)
    surface = ir["surfaces"][0].get("semanticSurface")
    if not isinstance(surface, Mapping) or not isinstance(surface.get("controls"), list):
        raise StandaloneProjectionError("Executable semantic control declarations are missing")
    if not ir.get("intents"):
        raise StandaloneProjectionError("Declared intents are absent")
    # All host-facade methods require *portable* implementations before semantic proof.
    intents = sorted({str(item.get("sourceName")) for item in ir["intents"]})
    if "" in intents or "None" in intents:
        raise StandaloneProjectionError("Intent sourceName is missing")
    projected = build_application_runtime_projection(repo, catalog, record)
    if projected.mount_mode != "host-bound":
        raise StandaloneProjectionError("This migration probe only supports existing host-bound packages")
    files = {
        "index.html": _HTML.encode(),
        "standalone.css": _CSS.encode(),
        "standalone.js": (repo / "main_computer/web/applications/scripts/mcel-standalone-package-host.js").read_bytes(),
        "contracts/layout.js": projected.files["contracts/layout.js"],
        "contracts/surface.js": projected.files["contracts/surface.js"],
        "contracts/intents.js": projected.files["contracts/intents.js"],
    }
    local_intents = ({"switchMode", "enterToken", "clearExpression", "evaluateExpression", "drawGraph", "resetGraph"}
        if app_id == "calculator" else {"editDraft", "discardDraft", "closeFile"})
    if portable:
        if app_id not in ("calculator", "code-editor"):
            raise StandaloneProjectionError("Portable execution requires an audited domain core")
        script_root = repo / "main_computer/web/applications/scripts"
        files["portable.js"] = (script_root / "mcel-standalone-portable-runtime.js").read_bytes()
        core_filename = "calculator-core.js" if app_id == "calculator" else "code-editor-core.js"
        files[core_filename] = (script_root / core_filename).read_bytes()
    manifest: dict[str, Any] = {
        "schema": SCHEMA, "appId": app_id,
        "source": {"semanticFingerprint": str(compiled.semantic_fingerprint),
                   "sourceSha256": _sha(source.read_bytes()),
                   "packageFingerprint": record.fingerprint,
                   "hostBoundProjectionFingerprint": projected.fingerprint},
        "presentationAuthority": "generated-from-compiled-layout-tree",
        "sourceHostAssetsIncluded": False,
        "layoutTree": layout,
        "semanticControls": surface["controls"],
        "declaredIntents": intents,
        "portableMode": bool(portable),
        "portableImplementations": ({name: ("local-pure-core" if name in local_intents else "authorized-capability")
                                    for name in intents} if portable else {}),
        "missingPortableIntents": [] if portable else intents,
        "behavioralParityProven": False,
        "fullSemanticRuntimeProven": False,
        "modules": {"layout": "./contracts/layout.js", "surface": "./contracts/surface.js", "intents": "./contracts/intents.js"},
        "assetDigests": {p: _sha(b) for p, b in sorted(files.items())},
    }
    files["presentation.json"] = _json_bytes(manifest)
    destination = Path(output_root).resolve() / app_id
    for relative, content in files.items():
        target = (destination / relative).resolve()
        if not target.is_relative_to(destination):
            raise StandaloneProjectionError("Unsafe generated path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return manifest
