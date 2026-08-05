from __future__ import annotations

import json
from pathlib import Path
from main_computer.mcel_package_test_support import logical_package_text


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_generated_surface_identity_connects_blueprint_contract_and_html() -> None:
    blueprint = json.loads((PACKAGE_ROOT / "blueprint.json").read_text(encoding="utf-8"))
    surface = logical_package_text("contract-counter", "contracts/surface.js")
    document = (PACKAGE_ROOT / "src" / "index.html").read_text(encoding="utf-8")

    assert blueprint["appId"] == "contract-counter"
    assert blueprint["rootSelector"] == "#contract-counter-app"
    assert 'surfaceId: "contract-counter.surface.primary"' in surface
    assert 'data-mcel-surface-id="contract-counter.surface.primary"' in document
    assert 'data-mcel-node-id="contract-counter.value"' in document
    assert 'data-mcel-intent-id="increment"' in document
    assert 'data-mcel-intent-id="reset"' in document
