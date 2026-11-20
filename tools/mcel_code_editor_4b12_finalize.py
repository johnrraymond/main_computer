"""Finish the 4B.1/4B.2 Code Editor authoring migration after archive extraction.

The archive overlay cannot delete the old root-level blueprint on extraction.
This command deletes that one obsolete authored presentation authority only after
verifying its known baseline digest and the complete new canonical pair.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT = ROOT / "mcel_apps/code-editor/blueprint.json"
EXPECTED_BLUEPRINT_SHA256 = "91a9890b5f82d2d0dcee4957df58b13ba4e975c870eaedfa32fd9aaf59df9b4a"


def finalize(root: Path = ROOT) -> dict:
    root = root.resolve()
    pkg = root / "mcel_apps/code-editor"
    blueprint = pkg / "blueprint.json"
    manifest = json.loads((pkg / "mcel.app.json").read_text(encoding="utf-8"))
    assert manifest["appId"] == "code-editor" and manifest["nativeRoute"] == "/applications/code-editor", "New manifest missing; extract patch first"
    assert manifest["template"] == {"id":"mcel.canonical-application-template","version":"2.0.0"}, "V2 template missing"
    assert (pkg / "application.html").is_file() and (pkg / "application.js").is_file(), "Canonical source pair incomplete"
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    compiled = compile_dsl_application(pkg / "application.js", write_candidate=False)
    assert compiled.valid and compiled.app_id == "code-editor", "Canonical source compilation failed"
    from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
    live_ir = compile_semantic_app("code-editor", root)
    assert live_ir["application"]["appId"] == "code-editor" and len(live_ir["intents"]) == 39, "Native host has not adopted the canonical source"
    ports = (root / "main_computer/web/applications/scripts/mcel-semantic-read-ports.js").read_text(encoding="utf-8")
    assert 'const allowedFor=appId=>appId==="code-editor"' in ports, "Canonical port authorization adapter missing"
    if blueprint.is_symlink():
        raise ValueError("Refusing to delete symlink blueprint")
    removed = False
    old_bytes = None
    if blueprint.exists():
        old_bytes = blueprint.read_bytes()
        digest = hashlib.sha256(old_bytes).hexdigest()
        if digest != EXPECTED_BLUEPRINT_SHA256:
            raise ValueError("Refusing to delete modified legacy blueprint; reconcile manually")
        blueprint.unlink()
        removed = True
    try:
        from main_computer.mcel_application_packages import build_application_package_catalog
        from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
        catalog = build_application_package_catalog(root)
        assert catalog.ok, "Repository application catalog invalid"
        projections = build_runtime_projection_set(root)
        native = next(p for p in projections.projections if p.app_id == "code-editor")
        assert native.mount_mode == "native-semantic-route", "Canonical native routing not registered"
    except BaseException:
        if removed and old_bytes is not None:
            blueprint.write_bytes(old_bytes)
        raise
    return {"status":"PASS_4B12_CANONICAL_READY", "blueprintRemoved":removed,
            "applicationId":compiled.app_id,"intents":len(compiled.normalized_ir["intents"]),
            "capabilities":len(compiled.normalized_ir["capabilities"]),
            "effects":len(compiled.normalized_ir["effects"]),
            "route":native.host_route}


if __name__ == "__main__":
    print("MCEL_CODE_EDITOR_4B12 " + json.dumps(finalize(),sort_keys=True))
