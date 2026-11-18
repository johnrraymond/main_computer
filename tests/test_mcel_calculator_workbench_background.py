"""The Calculator and other MCEL applications load the same background CSS."""
from pathlib import Path

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from main_computer.mcel_semantic_other_apps_host import CSS as WORKBENCH_CSS

ROOT = Path(__file__).resolve().parents[1]
STYLE = "main_computer/web/applications/styles/mcel-semantic-workbench.css"


def test_calculator_does_not_read_expert_css(monkeypatch):
    assert WORKBENCH_CSS == STYLE
    assert not (ROOT / "main_computer/web/applications/styles/mcel-semantic-expert.css").exists()
    original = Path.read_text
    seen = []

    def read(path, *args, **kwargs):
        if path.name == "mcel-semantic-expert.css":
            raise AssertionError("Calculator loaded the obsolete Expert background CSS")
        if path.name == "mcel-semantic-workbench.css":
            seen.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    html = render_expanded_page(ROOT, promoted=True)
    assert len(seen) == 1
    assert "Semantic Calculator" in html or "Main Computer Calculator" in html
    assert "Semantic Calculator ready" in html


def test_unified_background_scopes_do_not_overlap():
    css = (ROOT / STYLE).read_text(encoding="utf8")
    assert "/* Calculator semantic background rules (formerly expert.css). */" in css
    assert ":where(body.mcel-workbench-page)" in css
    assert ":where(body.mcel-expert-page)" in css
    assert "html:has(> body.mcel-workbench-page){color-scheme:dark}" in css
    assert "body.mcel-expert-page" in css
