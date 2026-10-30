"""14.7.7b regression: one original row expands as one continuous visual card."""
from pathlib import Path
from tools.mcel_row_atomic_hover_20_frob import verify

ROOT=Path(__file__).resolve().parents[1]


def test_individual_row_exclusively_owns_hover():
    js=(ROOT/'main_computer/web/applications/scripts/mcel-semantic-collection-workspace.js').read_text('utf-8')
    hosts=(ROOT/'main_computer/web/applications/scripts/mcel-presentation-hosts.js').read_text('utf-8')
    assert "row.dataset.mcelHoverOwner='row'" in js
    assert "overlayHost.openUnclip(entry.node,detail,lifecycle,collection,scrollHost)" in js
    assert "const collectionUnclip=null" in js
    assert 'source.showPopover()' in hosts
    assert "pane.style.visibility='hidden'" in hosts
    assert 'let plan=measure();' in hosts
    assert "source.style.setProperty('grid-template-rows'" in hosts
    assert "pane.style.position='absolute'" not in hosts[hosts.index('function openUnclip('):]


def test_20_independent_records_atomic_mouse_scroll_updates_and_dismissal(tmp_path):
    report=verify(tmp_path)
    assert report['status']=='PASS',[(case['width'],[k for k,v in case['checks'].items() if not v])
                                    for case in report['cases']]
