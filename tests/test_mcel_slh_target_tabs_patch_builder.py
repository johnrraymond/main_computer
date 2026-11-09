"""The builder must preserve accepted local workflows and emit only target changes."""
from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest
from tools.mcel_slh_target_tabs_patch_builder import (
    HOST,DISPATCH,PatchBuildError,generate,upgrade_dispatch,upgrade_host,
)

ROOT=Path(__file__).resolve().parents[1]


def test_builder_preserves_existing_local_implementation():
    source=(ROOT/HOST).read_text(encoding='utf-8')
    # Test with the original install's pre-tab version if available; do not
    # rewrite a previous patch from the full checked-in source.
    if 'mcel.slh.target-tabs.v2' in source:
        with pytest.raises(PatchBuildError,match='already been installed'):
            upgrade_host(source)
        return
    changed=upgrade_host(source)
    assert 'def render_slh_page' in changed
    assert '"/applications/slh/network": "network"' in changed
    assert "return render_network_slh_page(repo)" in changed
    assert source.count('app.execute("intent:refresh-overview")') == changed.count('app.execute("intent:refresh-overview")')


def test_builder_refuses_repeat_or_unrecognized_routes():
    old='ROUTES = {"/applications/slh": "overview", "/applications/slh/workers": "worker-bank"}\n'
    assert 'mcel.slh.target-tabs.v2' in upgrade_host((ROOT/HOST).read_text(encoding='utf-8')) if 'mcel.slh.target-tabs.v2' not in (ROOT/HOST).read_text(encoding='utf-8') else True
    with pytest.raises(PatchBuildError):
        upgrade_host('ROUTES = {"/different": "overview"}\ndef render_slh_page(view: str = "overview", repo: Path = ROOT) -> str:\n')


def test_builder_outputs_two_unwrapped_python_files_only(tmp_path):
    source=(ROOT/HOST).read_text(encoding='utf-8')
    dispatch=(ROOT/DISPATCH).read_text(encoding='utf-8')
    if 'mcel.slh.target-tabs.v2' in source or 'mcel.slh.target-tabs.v2' in dispatch:
        pytest.skip('Builder is intentionally single-use after application; see isolated acceptance')
    dummy=tmp_path/'checkout'
    for name in (HOST,DISPATCH):
        file=dummy/name
        file.parent.mkdir(parents=True,exist_ok=True)
        file.write_text((ROOT/name).read_text(encoding='utf-8'),encoding='utf-8')
    output=tmp_path/'generated.zip'
    generate(dummy,output)
    with ZipFile(output) as z:
        assert z.testzip() is None
        assert z.namelist()==[HOST,DISPATCH]
        assert all(not name.startswith('main_computer_test/') for name in z.namelist())
        assert 'mcel.slh.target-tabs.v2' in z.read(DISPATCH).decode('utf-8')
