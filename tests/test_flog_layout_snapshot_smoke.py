from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "main_computer" / "flog_layout_snapshot_smoke.py"


def load_module():
    spec = importlib.util.spec_from_file_location("flog_layout_snapshot_smoke", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_default_app_is_simple_file_explorer():
    module = load_module()

    available = module.enumerate_apps(REPO_ROOT)
    selected = module.parse_apps_arg("file-explorer", available)

    assert [entry.app for entry in selected] == ["file-explorer"]
    assert selected[0].root_selector == "#file-explorer-app"
    assert selected[0].partial.endswith("file-explorer.html")


def test_expand_applications_html_inlines_app_partial():
    module = load_module()

    expanded = module.expand_applications_html(REPO_ROOT)

    assert "@include applications/apps/file-explorer.html" not in expanded
    assert 'id="file-explorer-app"' in expanded
    assert "File Explorer" in expanded


def test_parse_viewports_accepts_named_dimensions():
    module = load_module()

    profiles = module.parse_viewports("desktop=1440x900,narrow=390x844")

    assert [(profile.name, profile.width, profile.height) for profile in profiles] == [
        ("desktop", 1440, 900),
        ("narrow", 390, 844),
    ]


def test_write_reports_separates_proved_inferred_and_unknown(tmp_path):
    module = load_module()

    report = {
        "kind": "mcel.flog.layout.snapshot.report",
        "generatedAt": "2026-07-09T00:00:00+00:00",
        "smokeLevel": "browser-geometry-human-review",
        "geometryEngine": "playwright-chromium",
        "hierarchySource": "html",
        "chrome": "current",
        "apps": ["file-explorer"],
        "viewports": [{"name": "desktop", "width": 1440, "height": 900}],
        "snapshotDirectory": ".",
        "snapshotFiles": ["file-explorer--desktop--current--viewport.png"],
        "measurements": [
            {
                "app": "file-explorer",
                "viewportProfile": "desktop",
                "chrome": "current",
                "rootSelector": "#file-explorer-app",
                "geometryFacts": {
                    "unclaimedAreaRatio": 0.25,
                    "meaningfulCoverageRatio": 0.75,
                    "rootViewportCoverageRatio": 0.5,
                    "clippedCriticalActionCount": 0,
                    "hiddenCriticalActionCount": 0,
                    "intentionallyDeferredActionCount": 0,
                    "scrollOwnerCount": 0,
                },
                "classification": {"score": 95, "status": "pass", "warnings": []},
                "inference": {"suggestion": "split-pane", "confidence": "medium"},
                "humanLoop": {
                    "proved": ["mount-root rectangle"],
                    "inferred": ["which stable layout family should be tried first"],
                    "unknowns": ["Concern hierarchy is weak."],
                },
                "snapshots": {"viewport": "file-explorer--desktop--current--viewport.png"},
            }
        ],
    }

    json_path, md_path = module.write_reports(report, tmp_path)
    md = md_path.read_text(encoding="utf-8")

    assert json_path.exists()
    assert "Proved by Chromium" in md
    assert "Inferred, not proved" in md
    assert "Unknown / needs human review" in md
    assert "file-explorer--desktop--current--viewport.png" in md
    assert "PNG files written: `1`" in md


def test_verify_png_written_requires_real_file(tmp_path):
    module = load_module()

    png = tmp_path / "proof.png"
    png.write_bytes(b"png-data")
    module.verify_png_written(png)

    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    try:
        module.verify_png_written(empty)
    except RuntimeError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("empty PNG should fail")

    try:
        module.verify_png_written(tmp_path / "missing.png")
    except RuntimeError as exc:
        assert "not created" in str(exc)
    else:
        raise AssertionError("missing PNG should fail")
