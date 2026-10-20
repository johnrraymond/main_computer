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

def test_viewport_uses_declared_layout_not_old_html():
    source = (ROOT / 'main_computer/viewport_pages.py').read_text(encoding='utf-8')
    assert 'if include_name == "applications/apps/onlyoffice.html":' in source
    assert 'from main_computer.mcel_onlyoffice_host_projection import project' in source

def test_no_onlyoffice_in_incomplete_package_tree():
    assert not (ROOT / 'mcel_apps/onlyoffice').exists()
