from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_generated_browser_code_uses_the_generic_package_mount() -> None:
    runtime = (PACKAGE_ROOT / "src" / "app.js").read_text(encoding="utf-8")

    assert "MCEL.mountApplicationPackage" in runtime
    assert "appId" in runtime
    assert "mcel.runtime.json" in runtime
    assert "canonicalState.count" not in runtime
    assert "count += 1" not in runtime
    assert "not implemented" not in runtime
