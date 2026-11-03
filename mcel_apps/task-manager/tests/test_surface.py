from __future__ import annotations

import json
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_generated_surface_identity_connects_blueprint_contract_and_html() -> None:
    blueprint = json.loads((PACKAGE_ROOT / "blueprint.json").read_text(encoding="utf-8"))
    surface = (PACKAGE_ROOT / "contracts" / "surface.js").read_text(encoding="utf-8")
    from main_computer.mcel_semantic_generated_host import compile_p3_generated_candidate

    assert blueprint["appId"] == "task-manager"
    assert blueprint["rootSelector"] == "#mcel-workbench"
    assert blueprint["presentationAuthority"]["kind"] == "host-bound-generated-semantic"
    assert blueprint["sourceHints"] == ["application.js"]
    # No authored HTML is allowed for this generated application.
    assert not (PACKAGE_ROOT / "application.html").exists()
    assert (PACKAGE_ROOT / "application.js").is_file()
    ir,composition=compile_p3_generated_candidate()
    assert ir["application"]["appId"] == "semantic-task-manager-generated-p3"
    assert ir["surfaces"][0]["purpose"] == "monitor"
    assert not ir.get("layouts") and not ir["surfaces"][0].get("htmlBindings")
    assert composition["fingerprint"]
    assert surface.strip()  # Retained package contract is not a generated HTML authority.
