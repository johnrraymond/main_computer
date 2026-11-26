"""ONLYOFFICE live host must derive from declarative layout, not stale HTML."""
import json
from pathlib import Path

from main_computer.mcel_onlyoffice_host_projection import project

ROOT = Path(__file__).resolve().parents[1]

def test_host_projection_preserves_critical_controls():
    html = project(ROOT)
    for marker in (
        'id="onlyoffice-app"', 'id="onlyoffice-editor-host"',
        'id="onlyoffice-diagnostics-counter"', 'id="onlyoffice-save-now"',
        'id="onlyoffice-reload-editor"', 'id="onlyoffice-file-list"',
    ):
        assert marker in html

def test_canonical_viewport_uses_native_package_not_old_html():
    source = (ROOT / 'main_computer/viewport_pages.py').read_text(encoding='utf-8')
    assert 'from main_computer.mcel_onlyoffice_host_projection import project' not in source
    assert (ROOT / 'mcel_apps/onlyoffice/application.js').is_file()
    assert 'render_onlyoffice_page' in (ROOT / 'main_computer/viewport_route_dispatch.py').read_text(encoding='utf-8')
