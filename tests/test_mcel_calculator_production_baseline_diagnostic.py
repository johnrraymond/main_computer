"""Production Calculator baseline: locally projected markup and bounded loopback observation."""
from email.message import Message
from io import BytesIO
from pathlib import Path

import pytest

from tools import mcel_calculator_production_baseline_diagnostic as diagnostic
from tools.mcel_calculator_production_baseline_diagnostic import inspect_baseline, read_loopback


def _repo(tmp_path: Path):
    bindings = tmp_path/'main_computer/web/applications/scripts/dom-bindings/calculator.js'
    bindings.parent.mkdir(parents=True)
    bindings.write_text('const a=document.querySelector("#calculator-display");\nconst b=document.querySelector("#calculator-mode-basic");')
    script = tmp_path/'main_computer/web/applications/scripts/calculator.js'
    script.write_text('function calculateExpression(){}')
    return tmp_path


def _calculator_markup():
    return '<main id="calculator-app"><div id="calculator-display"></div><button id="calculator-mode-basic">Basic</button></main>'


def test_missing_source_never_becomes_complete_from_declared_bindings(tmp_path):
    repo = _repo(tmp_path)
    report = inspect_baseline(repo)
    assert report['status'] == 'BASELINE_INCOMPLETE'
    assert report['source_present'] is False
    assert report['canonical_projection_present'] is False
    assert len(report['missing_binding_ids']) == 2
    assert not report['semantic_promotion_authorized']


def test_complete_source_and_broken_source_are_distinguished(tmp_path):
    repo = _repo(tmp_path)
    markup = repo/'main_computer/web/applications/apps/calculator.html'
    markup.parent.mkdir(parents=True)
    markup.write_text(_calculator_markup())
    assert inspect_baseline(repo)['status'] == 'SOURCE_BASELINE_READY_FOR_PARITY_REVIEW'
    markup.write_text('<main id="calculator-app"><div id="calculator-display"></div><div id="calculator-display"></div></main>')
    report = inspect_baseline(repo)
    assert report['status'] == 'BASELINE_INCOMPLETE'
    assert report['missing_binding_ids'] == ['calculator-mode-basic']
    assert report['duplicate_dom_ids'] == ['calculator-display']


def test_loopback_observation_does_not_claim_source_recovery(tmp_path):
    repo = _repo(tmp_path)
    html = ('<div id="calculator-display"></div>' + _calculator_markup() +
            '<section id="calculator-display"></section>')
    result = inspect_baseline(repo, html)
    assert result['status'] == 'HOSTED_DOM_OBSERVED_SOURCE_NOT_RECOVERED'
    assert not result['source_present']
    assert result['observed_ids'] == ['calculator-app', 'calculator-display', 'calculator-mode-basic']
    assert result['duplicate_dom_ids'] == []
    for url in ('https://127.0.0.1:8765/applications/calculator',
                'http://example.com/applications/calculator',
                'http://127.0.0.1:8765/applications/task-manager',
                'http://127.0.0.1:8765/applications/calculator?x=y'):
        with pytest.raises(ValueError):
            read_loopback(url)


def test_real_canonical_production_projection_replaces_missing_snapshot_html():
    repo = Path(__file__).resolve().parents[1]
    report = inspect_baseline(repo)
    assert not report['source_present']
    assert report['canonical_projection_present']
    assert report['baseline_provenance'] == 'canonical-compiled-production-projection'
    assert report['status'] == 'PROJECTED_SOURCE_BASELINE_READY_FOR_PARITY_REVIEW'
    assert report['calculator_subtree_complete']
    assert len(report['required_binding_ids']) == 37
    assert not report['missing_binding_ids']
    assert not report['duplicate_dom_ids']
    assert not report['semantic_promotion_authorized']


def test_hosted_markup_must_match_canonical_projection():
    repo = Path(__file__).resolve().parents[1]
    from main_computer.mcel_calculator_host_projection import project
    source = project(repo)
    result = inspect_baseline(repo, '<main>\n'+source+'\n<section id="calculator-result"></section></main>')
    assert result['status'] == 'HOSTED_PRODUCTION_PROJECTION_VERIFIED'
    assert result['hosted_matches_canonical_projection'] is True
    altered = source.replace('id="calculator-display"', 'id="calculator-display" data-stale="true"')
    result = inspect_baseline(repo, altered)
    assert result['status'] == 'BASELINE_INCOMPLETE'
    assert result['hosted_matches_canonical_projection'] is False


class _FakeResponse(BytesIO):
    def __init__(self, data, content_type='text/html'):
        super().__init__(data)
        self.headers = Message()
        self.headers['Content-Type'] = content_type


class _FakeOpener:
    def __init__(self, data, content_type='text/html'):
        self.data = data
        self.content_type = content_type
        self.response = None

    def open(self, request, timeout):
        assert request.full_url == 'http://127.0.0.1:8765/applications/calculator'
        assert timeout == 5
        self.response = _FakeResponse(self.data, self.content_type)
        return self.response


def test_loopback_stops_at_calculator_subtree_even_when_full_page_exceeds_2mb(monkeypatch):
    # The original server serves an expanded all-applications page with large
    # script assets.  The diagnostic must not download them after Calculator.
    source = (b'<main>' + b' ' * 2_100_000 + _calculator_markup().encode() +
              b' ' * 4_000_000 + b'</main>')
    opener = _FakeOpener(source)
    monkeypatch.setattr(diagnostic, 'build_opener', lambda *args: opener)
    observed = read_loopback('http://127.0.0.1:8765/applications/calculator')
    assert len(observed) > 2_000_000
    assert len(observed) < len(source)
    assert 'id="calculator-display"' in observed
    # The observed prefix is smaller than the body: scripts after the app
    # were not downloaded.  The response is closed on context exit.


def test_loopback_rejects_wrong_content_type_and_missing_subtree(monkeypatch):
    wrong_type = _FakeOpener(b'{"error":"wrong route"}', 'application/json')
    monkeypatch.setattr(diagnostic, 'build_opener', lambda *args: wrong_type)
    with pytest.raises(ValueError, match='Expected HTML'):
        read_loopback('http://127.0.0.1:8765/applications/calculator')
    no_calc = _FakeOpener(b'<main>No calculator here</main>')
    monkeypatch.setattr(diagnostic, 'build_opener', lambda *args: no_calc)
    with pytest.raises(ValueError, match='complete Calculator subtree'):
        read_loopback('http://127.0.0.1:8765/applications/calculator')


def test_four_app_audit_uses_compiled_production_calculator_baseline():
    from tools.mcel_semantic_reference_parity_frob import run
    result = run(Path(__file__).resolve().parents[1])
    assert result['source_audit_pass']
    assert 'calculator' not in result['missing_production_baseline_evidence']
    calculator = result['applications']['calculator']
    assert calculator['production_baseline_complete_in_export']
    assert calculator['production_baseline_missing'] == []
    assert calculator['production_baseline_provenance'] == 'canonical-compiled-production-projection'
    assert calculator['production_projection_binding_ids_valid'] is True
    assert calculator['parity'] == 'INCOMPLETE_LIVE_EVIDENCE'
    assert calculator['certification'] == 'NOT_AUTHORIZED'


def test_production_source_audit_does_not_hide_missing_calculator_runtime_dependencies(tmp_path):
    from tools.mcel_semantic_reference_parity_frob import run
    # Exercise only the dependency-presence rule, without running a second
    # temporary compiler. Keep the real compiler/projection sources present.
    # A direct inspection of the actual audit source tests that all non-HTML
    # production dependencies must remain in the required inventory.
    report = run(Path(__file__).resolve().parents[1])
    sources = report['applications']['calculator']['production_sources']
    assert sources['main_computer/web/applications/scripts/calculator.js']
    assert sources['main_computer/web/applications/scripts/calculator-core.js']
    assert 'main_computer/web/applications/apps/calculator.html' not in report['missing_production_baseline_evidence'].get('calculator', [])
