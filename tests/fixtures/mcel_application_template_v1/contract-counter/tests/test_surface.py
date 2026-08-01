from __future__ import annotations

import json
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_generated_surface_identity_connects_blueprint_contract_and_html() -> None:
    blueprint = json.loads((PACKAGE_ROOT / "blueprint.json").read_text(encoding="utf-8"))
    surface = (PACKAGE_ROOT / "contracts" / "surface.js").read_text(encoding="utf-8")
    document = (PACKAGE_ROOT / "src" / "index.html").read_text(encoding="utf-8")

    assert blueprint["appId"] == "contract-counter"
    assert blueprint["rootSelector"] == "#contract-counter-app"
    assert 'surfaceId: "contract-counter.surface.primary"' in surface
    assert 'data-mcel-surface-id="contract-counter.surface.primary"' in document
    assert 'data-mcel-node-id="contract-counter.value"' in document
    assert 'data-mcel-intent-id="increment"' in document
    assert 'data-mcel-intent-id="reset"' in document
