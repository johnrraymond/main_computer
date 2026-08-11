from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERIC_DISPATCH_TEST = ROOT / "tests/test_mcel_generic_promotion_dispatch.py"


def test_reference_app_promotion_coverage_has_generic_dispatch_successor() -> None:
    assert GENERIC_DISPATCH_TEST.exists()

    text = GENERIC_DISPATCH_TEST.read_text(encoding="utf-8")
    assert "test_generic_promotion_dispatch_inspects_promoted_profile_backed_app" in text
    assert "test_generic_promotion_rehearsal_for_promoted_app_is_non_mutating" in text
