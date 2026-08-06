from __future__ import annotations

import json
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]


def test_calculator_authoritative_package_contains_authored_source_only() -> None:
    manifest = json.loads((PACKAGE / "mcel.app.json").read_text(encoding="utf-8"))
    assert manifest["appId"] == "calculator"
    assert manifest["authoring"]["status"] == "dsl-authoritative"
    assert manifest["projection"]["presentationAuthority"] == "existing-host-html"
    assert manifest["projection"]["legacySemanticAdapterRetired"] is True
    assert not (PACKAGE / "contracts").exists()
    assert not (PACKAGE / "generated").exists()
    assert not (PACKAGE / "mcel.generated.json").exists()
    assert not (PACKAGE / "src").exists()
