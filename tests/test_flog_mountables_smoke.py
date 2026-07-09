from __future__ import annotations

from pathlib import Path

from main_computer.flog_mountables_smoke import build_report, render_markdown


ROOT = Path(__file__).resolve().parents[1]


def test_flog_mountables_smoke_reports_all_user_mountables() -> None:
    report = build_report(ROOT)

    apps = {item["app"]: item for item in report["mountables"]}

    assert report["kind"] == "mcel.flog.mountables.report"
    assert report["source"]["hierarchySource"] == "html"
    assert "desktop" in apps
    assert "conductor" in apps
    assert "game-editor" in apps
    assert "layout-builder" not in apps
    assert apps["game-editor"]["aliasOfPartial"] == "layout-builder"
    assert apps["conductor"]["hierarchySource"] == "html"
    assert apps["conductor"]["stableLayouts"]
    assert apps["conductor"]["recommendedDefaults"]["desktop"] in report["layoutFamilies"]


def test_flog_mountables_smoke_markdown_has_reproducibility_sections() -> None:
    report = build_report(ROOT)
    markdown = render_markdown(report)

    assert "# FLOG Mountables Smoke Report" in markdown
    assert "## Mountables" in markdown
    assert "Recommended defaults" in markdown
    assert "`conductor`" in markdown
    assert "Reproducibility notes" in markdown
