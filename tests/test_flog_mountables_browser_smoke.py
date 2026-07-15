from __future__ import annotations

from pathlib import Path

from main_computer.flog_mountables_browser_smoke import (
    BROWSER_MEASURE_JS,
    classify_measurement,
    parse_mountables,
    parse_viewports,
    render_markdown,
    summarize_app,
)


ROOT = Path(__file__).resolve().parents[1]


def test_browser_smoke_static_mountable_inventory_has_shell_aliases() -> None:
    specs = {item.app: item for item in parse_mountables(ROOT)}

    assert "desktop" in specs
    assert "conductor" in specs
    assert "game-editor" in specs
    assert specs["desktop"].root_selector == ".viewport"
    assert specs["game-editor"].root_selector == "#game-editor-app"
    assert specs["game-editor"].alias_of_partial == "layout-builder"
    assert specs["webgl"].root_selector == "#webgl-demo"


def test_browser_smoke_viewport_parser_accepts_named_profiles() -> None:
    profiles = parse_viewports("desktop=1440x900,narrow=390x844")

    assert [(item.name, item.width, item.height) for item in profiles] == [
        ("desktop", 1440, 900),
        ("narrow", 390, 844),
    ]


def test_browser_smoke_classification_flags_geometry_pressure() -> None:
    measurement = {
        "root": {"area": 1000},
        "metrics": {
            "clippedCriticalActions": 1,
            "hiddenCriticalActions": 1,
            "scrollOwnerCount": 5,
            "unclaimedLeafAreaRatio": 0.72,
            "viewportCoverageRatio": 0.1,
            "documentOverflowX": True,
            "documentOverflowY": False,
        },
    }

    result = classify_measurement(measurement)

    assert result["status"] == "fail"
    assert result["score"] < 65
    assert any("clipped" in warning for warning in result["warnings"])
    assert any("unclaimed" in warning for warning in result["warnings"])


def test_browser_smoke_markdown_reports_browser_geometry_summary() -> None:
    measurement = {
        "app": "conductor",
        "viewportProfile": "desktop",
        "chrome": "current",
        "root": {"area": 1000},
        "metrics": {
            "clippedCriticalActions": 0,
            "hiddenCriticalActions": 0,
            "scrollOwnerCount": 1,
            "unclaimedLeafAreaRatio": 0.2,
            "viewportCoverageRatio": 0.7,
            "documentOverflowX": False,
            "documentOverflowY": False,
        },
    }
    measurement["classification"] = classify_measurement(measurement)
    summary = summarize_app([measurement])
    report = {
        "generatedAt": "2026-07-09T00:00:00+00:00",
        "smokeLevel": "browser-geometry",
        "geometryEngine": "playwright-chromium",
        "hierarchySource": "html",
        "summary": {
            "mountableCount": 1,
            "measurementCount": 1,
            "statusCounts": {"pass": 1},
            "apps": {"conductor": summary},
            "notes": ["External CDN/network requests are blocked so the smoke remains reproducible and local."],
        },
        "mountables": [measurement],
    }

    markdown = render_markdown(report)

    assert "FLOG Mountables Browser Geometry Smoke Report" in markdown
    assert "playwright-chromium" in markdown
    assert "unclaimedLeaf" in markdown

def test_browser_smoke_reports_edge_rounded_device_pixel_rectangles() -> None:
    js = BROWSER_MEASURE_JS

    assert "pixelGeometry: \"css-edge-rounded-device-pixels-v1\"" in js
    assert "Math.round(leftCss * devicePixelRatio)" in js
    assert "Math.round(rightCss * devicePixelRatio)" in js
    assert "return withPixelRect({x: left, y: top, left, top, right, bottom, width, height, area: width * height});" in js
