"""CSS retirement frob: exact source spans, bounded real-browser differential."""
from __future__ import annotations

from pathlib import Path
from io import BytesIO
from PIL import Image

from tools.mcel_css_retirement_frob import (
    WORKBENCH, declarations, responsibility, audit, _pixel_diff,
)

ROOT = Path(__file__).resolve().parents[1]


def test_source_spans_identify_one_declaration_even_with_duplicates_and_media():
    css = '''body { padding: 7px; padding: 3px; color: red }
@media (max-width: 600px) { body { padding: 0 !important; background: url("x;y"); } }'''
    entries = declarations(css, "test.css")
    assert [(e["property"], e["line"], e["important"]) for e in entries] == [
        ("padding", 1, False), ("padding", 1, False), ("color", 1, False),
        ("padding", 2, True), ("background", 2, False),
    ]
    assert entries[3]["conditions"] == [("media", "(max-width: 600px)")]
    for i, r in enumerate(entries):
        removed = css[:r["start"]] + css[r["end"]:]
        remaining = declarations(removed, "test.css")
        assert len(remaining) == len(entries)-1, (i, r, removed)
        if i == 0:
            assert remaining[0]["value"] == "3px"  # only FIRST padding removed
        if i == 1:
            assert remaining[0]["value"] == "7px"  # only SECOND padding removed


def test_existing_style_files_are_fully_source_located():
    for relpath in (WORKBENCH,):
        css = (ROOT / relpath).read_text(encoding="utf-8")
        items = declarations(css, relpath)
        assert len(items) > 200
        assert any(x["conditions"] for x in items)
        assert len({x["id"] for x in items}) == len(items)
        for r in items:
            fragment = css[r["start"]:r["end"]]
            assert fragment.strip().startswith(r["property"] + ":"), (r["id"], fragment[:60])
            assert r["property"] in fragment
            assert r["line"] == css.count("\n", 0, r["start"]) + 1


def test_owner_classification_is_a_review_not_a_claim_of_replacement():
    assert responsibility("color") == "Theme"
    assert responsibility("padding-top") == "Form/Layout"
    assert responsibility("background-image") == "Chrome"
    assert responsibility("cursor") == "application/interaction"


def test_real_chromium_differential_does_not_modify_css(tmp_path):
    before = {WORKBENCH: (ROOT / WORKBENCH).read_bytes()}
    out = tmp_path / "audit"
    # Probe the shared stylesheet in both the Calculator and SLH hosts.
    for app in ("calculator", "slh"):
        result = audit(ROOT, out / app, apps=[app], widths=[1040], limit=6,
                       stability_every=1)
        assert len(result) == 6
        assert all(r["file"] == WORKBENCH for r in result)
        assert sum(r["tested_states"] for r in result) >= 1, result
        # Unstable restoration is reported as UNVERIFIED rather than as a deletion.
        assert all(r["decision"].startswith("UNVERIFIED") for r in result if r["errors"]), result
        assert (out / app / "results.json").is_file()
        assert (out / app / "results.csv").is_file()
    assert {name: (ROOT / name).read_bytes() for name in before} == before


def test_png_compare_is_exact_to_single_changed_pixel():
    a = Image.new("RGB", (3, 3), (10, 20, 30))
    b = a.copy()
    b.putpixel((1, 2), (10, 20, 31))
    def png(image):
        data = BytesIO()
        image.save(data, format="PNG")
        return data.getvalue()
    assert _pixel_diff(png(a), png(a)) == (0, None)
    assert _pixel_diff(png(a), png(b)) == (1, [1, 2, 2, 3])
