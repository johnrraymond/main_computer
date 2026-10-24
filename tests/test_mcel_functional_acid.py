"""Functional acid-test honesty, inventory completeness and PNG authority boundaries."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import mcel_functional_acid as acid


def test_full_source_inventory_includes_unproven_components():
    modules = acid.inventory_modules(acid.ROOT)
    paths = {entry['path'] for entry in modules}
    assert 'main_computer/web/applications/scripts/mcel-engine.js' in paths
    assert 'main_computer/web/applications/scripts/mcel-tab-workspace.js' in paths
    assert 'main_computer/web/applications/scripts/mcel-element-registry.js' in paths
    assert 'main_computer/mcel_dsl_compiler.py' in paths
    assert 'main_computer/web/applications/styles/mcel-lab.css' in paths
    assert len(paths) == len(modules)
    assert all(item['implementationStatus']=='UNKNOWN' for item in modules)
    assert all(item['kind']=='source-inventory' for item in modules)
    assert any(item['path']=='tools/mcel_functional_acid.py' for item in modules)
    assert any(item['path']=='main_computer/web/applications/scripts/task-manager-mcel.js'
               for item in modules)
    assert all('functionalStatus' not in item for item in modules)
    assert all(len(item['sha256'])==64 for item in modules)


def test_inventory_mode_never_makes_global_pass_claim(tmp_path):
    report = acid.run(acid.ROOT, output=tmp_path, mode='inventory')
    assert report['status']=='INCOMPLETE'
    assert report['counts']['FAIL']==0
    assert report['coverage']['elements']['registered'] is None
    assert report['coverage']['sourceInventory']['behaviorClaimsFromFilenames']==0
    assert len(report['modules']) > 100
    assert report['taskManagerFrozen'] is True
    assert report['checks'][-1]['id']=='source-immutable'
    assert report['checks'][-1]['status']=='PASS'
    assert (tmp_path/'report.json').exists()
    assert json.loads((tmp_path/'report.json').read_text())['status']=='INCOMPLETE'


def test_visual_screenshot_requires_explicit_baseline(tmp_path):
    from PIL import Image, ImageChops
    path=tmp_path/'example.png'
    Image.new('RGB',(24,32),(240,240,240)).save(path)
    pending=acid._compare_png(path,None,False,Image,ImageChops)
    assert pending['status']=='UNVERIFIED'
    baseline=tmp_path/'reviewed'
    assert acid._compare_png(path,baseline,False,Image,ImageChops)['reason']=='approved-baseline-missing'
    captured=acid._compare_png(path,baseline,True,Image,ImageChops)
    assert captured['status']=='UNVERIFIED'
    assert (baseline/path.name).is_file()
    admitted=acid._compare_png(path,baseline,False,Image,ImageChops)
    assert admitted['status']=='PASS'
    assert admitted['reason']=='pixel-exact-match'
    Image.new('RGB',(24,32),(20,20,20)).save(path)
    refused=acid._compare_png(path,baseline,False,Image,ImageChops)
    assert refused['status']=='FAIL'
    assert refused['reason']=='visual-diff'
    assert refused['changedBounds'] is not None
    Image.new('RGB',(20,32),(20,20,20)).save(path)
    assert acid._compare_png(path,baseline,False,Image,ImageChops)['reason']=='screenshot-dimensions-mismatch'


def test_run_never_promotes_missing_element_behavior(tmp_path,monkeypatch):
    monkeypatch.setattr(acid,'_run_pytest',lambda *a,**k: acid.result('unit','PASS'))
    monkeypatch.setattr(acid,'_run_browser',lambda *a,**k: {
        'status':'PASS','checks':[acid.result('geometry','PASS')],
        'elements':[{'id':'element.fake','family':'core','registryContract':'PASS',
                     'showcasePresence':'PASS','functionalBehavior':'UNVERIFIED',
                     'registrySerialization':'PASS','implementationStatus':'UNKNOWN'}],
        'visuals':[{'name':'fake.png','status':'UNVERIFIED'}]})
    report=acid.run(acid.ROOT,output=tmp_path,mode='core')
    assert report['status']=='INCOMPLETE'
    assert report['elements'][0]['functionalBehavior']=='UNVERIFIED'
    assert report['coverage']['elements']['behaviorUnverified']==1
    assert report['counts']['PASS'] > 0
    assert report['counts']['NOT_IMPLEMENTED']==0
    assert report['coverage']['sourceInventory']['filesDiscovered'] == len(report['modules'])


def test_real_failed_observation_does_not_get_overridden(tmp_path,monkeypatch):
    monkeypatch.setattr(acid,'_run_pytest',lambda *a,**k: acid.result('unit','PASS'))
    monkeypatch.setattr(acid,'_run_browser',lambda *a,**k: {
        'status':'FAIL','checks':[acid.result('geometry','FAIL')],
        'elements':[],'visuals':[]})
    report=acid.run(acid.ROOT,output=tmp_path,mode='core')
    assert report['status']=='FAIL'
    assert report['counts']['FAIL'] >= 1


def test_missing_test_selectors_are_refused():
    report=acid._run_pytest(acid.ROOT,['tests/not-real-proof.py'],timeout=1)
    assert report['status']=='FAIL'


def test_browser_coverage_is_live_in_chromium(tmp_path):
    pytest.importorskip('playwright.sync_api')
    browser=acid._run_browser(acid.ROOT,tmp_path,None,approve=False,chromium=None)
    if browser.get('error') == 'Playwright not installed':
        pytest.skip(browser['error'])
    specimen_available = all((acid.ROOT / path).is_file()
                             for path in (acid.NOTEBOOK, acid.FIXTURE))
    assert browser['status']==('FAIL' if any(x['status']=='FAIL' for x in browser['checks'])
                               else 'PASS' if specimen_available else 'UNVERIFIED'), browser.get('error')
    assert len(browser['elements']) > 90
    assert all(item['registrySerialization']=='PASS' for item in browser['elements'])
    assert any(item['showcasePresence']=='UNVERIFIED' for item in browser['elements'])
    assert all(item['functionalBehavior']=='UNVERIFIED' for item in browser['elements'])
    assert len(browser['visuals']) == (4 if specimen_available else 2)
    assert all(Path(x['path']).is_file() for x in browser['visuals'])
    assert all(x['status']=='UNVERIFIED' for x in browser['visuals'])
    assert any(x['id']=='core-toolkit-public-boundary-behavior' for x in browser['checks'])


def test_missing_frozen_notebook_does_not_abort_element_browser_proof(tmp_path, monkeypatch):
    pytest.importorskip('playwright.sync_api')
    monkeypatch.setattr(acid, 'NOTEBOOK', Path('not-in-repository/absent-notebook.js'))
    monkeypatch.setattr(acid, 'FIXTURE', Path('not-in-repository/absent-data.json'))
    outcome = acid._run_browser(acid.ROOT, tmp_path, None, approve=False, chromium=None)
    if outcome['status']=='UNVERIFIED' and outcome.get('error')=='Playwright not installed':
        pytest.skip('Playwright unavailable')
    assert outcome['status']==('FAIL' if any(x['status']=='FAIL' for x in outcome['checks'])
                               else 'UNVERIFIED'), outcome.get('error')
    assert len(outcome['elements']) > 90
    assert any(x['id']=='notebook-specimen' and x['status']=='UNVERIFIED'
               for x in outcome['checks'])
    assert any(x['id']=='registry-contracts' and x['status']=='PASS'
               for x in outcome['checks'])
    assert {x['name'] for x in outcome['visuals']} == {
        'element-showcase-1280.png','element-showcase-390.png'}


def test_present_but_invalid_frozen_notebook_is_a_failure(tmp_path, monkeypatch):
    pytest.importorskip('playwright.sync_api')
    # Both input files are present; compilation failure must never be treated
    # as an optional missing specimen or an admissible functional witness.
    monkeypatch.setattr(acid, 'NOTEBOOK', Path('tools/mcel_functional_acid.py'))
    monkeypatch.setattr(acid, 'FIXTURE', Path('tools/mcel_functional_acid.py'))
    def invalid(*args, **kwargs):
        raise ValueError('deliberately invalid DSL')
    monkeypatch.setattr(acid, 'compile_notebook_preview', invalid)
    outcome = acid._run_browser(acid.ROOT, tmp_path, None, approve=False, chromium=None)
    if outcome['status']=='UNVERIFIED' and outcome.get('error')=='Playwright not installed':
        pytest.skip('Playwright unavailable')
    assert outcome['status']=='FAIL', outcome.get('error')
    assert any(x['id']=='notebook-specimen' and x['status']=='FAIL'
               for x in outcome['checks'])
    assert any(x['id']=='registry-contracts' and x['status']=='PASS'
               for x in outcome['checks'])


def test_default_acid_report_path_is_a_directory():
    assert acid.REPORT_PATH.as_posix() == 'runtime/reports/mcel-functional-acid'


def test_legacy_report_json_directory_is_preserved_before_inventory(tmp_path):
    # Old runner created report.json/ as a directory holding evidence. New
    # runner must not attempt to write through it or delete prior screenshots.
    output = tmp_path / 'reports'
    old = output / 'report.json'
    (old / 'screenshots').mkdir(parents=True)
    (old / 'screenshots' / 'old.png').write_bytes(b'old screenshot evidence')
    (old / 'report.md').write_text('historical report', encoding='utf-8')
    report = acid.run(acid.ROOT, output=output, mode='inventory')
    archive = output / 'report-json-legacy'
    assert archive.is_dir()
    assert (archive / 'screenshots' / 'old.png').read_bytes() == b'old screenshot evidence'
    assert (archive / 'report.md').read_text(encoding='utf-8') == 'historical report'
    assert report['legacyOutputPreservedAt'] == str(archive)
    assert (output / 'report.json').is_file()
    assert json.loads((output / 'report.json').read_text())['status'] == 'INCOMPLETE'
    assert report['status'] == 'INCOMPLETE'
    # Second run must leave the preserved directory alone.
    again = acid.run(acid.ROOT, output=output, mode='inventory')
    assert again['legacyOutputPreservedAt'] is None
    assert (archive / 'screenshots' / 'old.png').read_bytes() == b'old screenshot evidence'


def test_legacy_archive_collision_never_overwrites_existing_report(tmp_path):
    output = tmp_path / 'reports'
    old = output / 'report.json'
    old.mkdir(parents=True)
    (old / 'old.txt').write_text('unmigrated')
    (output / 'report-json-legacy').mkdir()
    (output / 'report-json-legacy' / 'old.txt').write_text('existing archive')
    report = acid.run(acid.ROOT, output=output, mode='inventory')
    assert (output / 'report-json-legacy' / 'old.txt').read_text() == 'existing archive'
    assert (output / 'report-json-legacy-2' / 'old.txt').read_text() == 'unmigrated'
    assert report['legacyOutputPreservedAt'] == str(output / 'report-json-legacy-2')
    assert (output / 'report.json').is_file()


def test_regular_report_json_file_is_not_migrated(tmp_path):
    output = tmp_path / 'reports'
    output.mkdir()
    (output / 'report.json').write_text('old report', encoding='utf-8')
    report = acid.run(acid.ROOT, output=output, mode='inventory')
    assert report['legacyOutputPreservedAt'] is None
    assert not (output / 'report-json-legacy').exists()
    assert json.loads((output / 'report.json').read_text())['status'] == 'INCOMPLETE'


def test_symlink_report_target_is_refused_without_following(tmp_path):
    output = tmp_path / 'reports'
    output.mkdir()
    other = tmp_path / 'sensitive'
    other.mkdir()
    (other / 'old.txt').write_text('preserved')
    try:
        (output / 'report.json').symlink_to(other, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('Symlinks unavailable in this environment')
    with pytest.raises(OSError, match='symbolic link'):
        acid.run(acid.ROOT, output=output, mode='inventory')
    assert (other / 'old.txt').read_text() == 'preserved'


def test_showcase_self_reported_pass_cannot_certify_missing_or_collapsed_dom():
    evidence = {
        'defs':[{'id':'element.real'}, {'id':'element.phantom'}],
        'acidIds':['element.real','element.phantom'],
        'observed':[{'id':'element.real','width':200,'height':50}],
        'acid':{'status':'pass'}, 'duplicateIds':[],
    }
    result = acid.assess_showcase(evidence)
    assert not result['ok']
    assert 'declared-showcase-card-mismatch' in result['failures']
    evidence['observed'].append({'id':'element.phantom','width':0,'height':0,'visible':True})
    result = acid.assess_showcase(evidence)
    assert not result['ok']
    assert 'nonvisible-card-geometry' in result['failures']
    evidence['observed'][1] = {'id':'element.phantom','width':180,'height':55}
    assert acid.assess_showcase(evidence)['ok']
    # Even a real card is only presentation evidence. No functional witness is
    # admitted here, so no independently executed behavior is certified.


def test_unregistered_showcase_is_rejected_even_when_showcase_reports_pass():
    evidence = {
        'defs':[{'id':'element.one'}], 'acidIds':['element.one','element.invented'],
        'observed':[{'id':'element.one','width':80,'height':25},
                    {'id':'element.invented','width':90,'height':25}],
        'acid':{'status':'pass'}, 'duplicateIds':[]}
    assert 'showcase-references-unregistered-element' in acid.assess_showcase(evidence)['failures']


def test_module_inventory_is_not_counted_as_functionality(tmp_path, monkeypatch):
    monkeypatch.setattr(acid,'_run_pytest',lambda *a,**k: acid.result('unit','PASS'))
    monkeypatch.setattr(acid,'_run_browser',lambda *a,**k: {
        'status':'PASS','checks':[acid.result('present','PASS')],
        'elements':[{'id':'element.missing-behavior','family':'core',
                     'registrySerialization':'PASS','showcasePresence':'PASS',
                     'functionalBehavior':'UNVERIFIED','implementationStatus':'UNKNOWN'}],
        'visuals':[]})
    report=acid.run(acid.ROOT,output=tmp_path,mode='core')
    assert report['counts']['PASS']==len(acid.CORE_SUITES)+1  # suites plus source-immutable observation
    assert report['counts']['UNVERIFIED']==2  # optional notebook integration absent from this export
    assert report['status']=='INCOMPLETE'
    assert report['coverage']['elements']['behaviorUnverified']==1
    assert report['coverage']['sourceInventory']['behaviorClaimsFromFilenames']==0
    assert len(report['modules'])>150
    assert all('functionalStatus' not in m for m in report['modules'])


def test_real_functional_failure_has_priority_over_unverified(tmp_path, monkeypatch):
    monkeypatch.setattr(acid,'_run_pytest',lambda *a,**k: acid.result('unit','PASS'))
    monkeypatch.setattr(acid,'_run_browser',lambda *a,**k: {
        'status':'FAIL', 'checks':[acid.result('behavior-observed','FAIL')],
        'elements':[{'id':'element.sample','family':'core',
                     'registrySerialization':'PASS','showcasePresence':'PASS',
                     'functionalBehavior':'UNVERIFIED','implementationStatus':'UNKNOWN'}],
        'visuals':[]})
    report=acid.run(acid.ROOT,output=tmp_path,mode='core')
    assert report['status']=='FAIL'
    assert report['counts']['FAIL']==1
    assert report['coverage']['elements']['behaviorUnverified']==1


def test_pytest_sessionstart_failure_is_unverified_infrastructure(monkeypatch):
    class Process:
        returncode = 3
        stdout = 'INTERNALERROR> pytest_sessionstart failed to build exported catalog'
        stderr = 'InvalidRuntimeProjectionSource'
    monkeypatch.setattr(acid.subprocess, 'run', lambda *args, **kwargs: Process())
    report=acid._run_pytest(acid.ROOT,['tests/test_mcel_functional_acid.py'],timeout=30)
    assert report['status']=='UNVERIFIED'
    assert report['evidence']['executionClassification']=='infrastructure-error'


def test_pytest_real_assertion_failure_is_not_hidden(monkeypatch):
    class Process:
        returncode = 1
        stdout = '1 failed in 0.12s'
        stderr = 'AssertionError: deliberate regression'
    monkeypatch.setattr(acid.subprocess, 'run', lambda *args, **kwargs: Process())
    report=acid._run_pytest(acid.ROOT,['tests/test_mcel_functional_acid.py'],timeout=30)
    assert report['status']=='FAIL'
    assert report['evidence']['executionClassification']=='assertion-or-success'


def test_inventory_prunes_patch_run_time_capsules(tmp_path):
    source = tmp_path / 'main_computer' / 'mcel_real.py'
    source.parent.mkdir(parents=True)
    source.write_text('REAL=1', encoding='utf-8')
    tool = tmp_path / 'tools' / 'mcel_real.py'
    tool.parent.mkdir()
    tool.write_text('REAL_TOOL=1', encoding='utf-8')
    historical = (tmp_path/'tools/patching/reports/new_patch_runs' /
                  '20261024-1234-test/undo_bundle/files/tools/mcel_undo.py')
    historical.parent.mkdir(parents=True)
    historical.write_text('COPY=1', encoding='utf-8')
    nested = (tmp_path/'tools/patching/reports/new_patch_runs' /
              '20261024-1234-test/extracted/main_computer_test/tools/mcel_extracted.py')
    nested.parent.mkdir(parents=True)
    nested.write_text('COPY=2', encoding='utf-8')
    paths = {x['path'] for x in acid.inventory_modules(tmp_path)}
    assert paths == {'main_computer/mcel_real.py', 'tools/mcel_real.py'}


def test_inventory_never_follows_external_symlink(tmp_path):
    external = tmp_path/'elsewhere'
    external.mkdir()
    (external/'mcel_private.py').write_text('EXTERNAL=1')
    tools = tmp_path/'tools'
    tools.mkdir()
    try:
        (tools/'linked_dir').symlink_to(external, target_is_directory=True)
        (tools/'mcel_linked.py').symlink_to(external/'mcel_private.py')
    except (OSError, NotImplementedError):
        pytest.skip('This platform does not permit symlinks')
    assert acid.inventory_modules(tmp_path) == []


def test_public_api_contracts_execute_per_registered_element(tmp_path):
    pytest.importorskip('playwright.sync_api')
    outcome = acid._run_browser(acid.ROOT, tmp_path, None,
                                approve=False, chromium=None)
    assert len(outcome['elements']) == 141
    per_element = next(x for x in outcome['checks']
                       if x['id'] == 'per-element-semantic-contracts')
    assert per_element['status'] == 'PASS', per_element
    assert per_element['evidence']['exercised'] == 141
    assert per_element['evidence']['probeAssertions'] == 141 * 8
    assert len({x['id'] for x in outcome['elements']}) == 141
    for element in outcome['elements']:
        assert element['semanticContractExecution'] == 'PASS', element
        assert len(element['semanticContractProbes']) == 8
        assert all(p['status'] == 'PASS' for p in element['semanticContractProbes'])
        assert element['functionalBehavior'] == 'UNVERIFIED'
        assert 'render-or-model-consumption' in element['missingBehavioralWitnesses']
    assert 'showcase-subset-consistency' in {c['id'] for c in outcome['checks']}
    assert not any(c['id'] == 'showcase-declared-coverage' for c in outcome['checks'])


def test_element_contract_negative_control_detects_broken_real_registry():
    """A sabotaged live registry operation must FAIL one element, not pass 141."""
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        executable = acid.shutil.which('chromium') or acid.shutil.which('chromium-browser')
        browser = p.chromium.launch(headless=True,
                                     **({'executable_path':executable,
                                         'args':['--no-sandbox']} if executable else {}))
        try:
            page = browser.new_page()
            for name in ('mcel-element-registry.js', 'mcel-elements-core.js'):
                page.add_script_tag(content=(acid.ROOT/acid.SOURCE/name).read_text())
            page.add_script_tag(content=(acid.ROOT/'tools/mcel_element_contract_probe.js').read_text())
            def invoke():
                return page.evaluate('() => window.McelAcidElementContracts.run()')
            before = invoke()
            assert all(e['status'] == 'PASS' for e in before['elements'])
            target = before['elements'][0]['id']
            page.evaluate('''target => {
                const registry = window.McelElementRegistry;
                const original = registry.serializeElement;
                registry.serializeElement = function(id, props) {
                    const output = original(id, props);
                    if (id === target) output.family = 'wrong-family';
                    return output;
                };
            }''', target)
            after = invoke()
            failed = [e for e in after['elements'] if e['status'] == 'FAIL']
            assert len(failed) == 1
            assert failed[0]['id'] == target
            assert {x['id'] for x in failed[0]['probes'] if x['status']=='FAIL'} == {
                'source-metadata-serializes'}
            assert len(after['elements']) == len(before['elements'])
        finally:
            browser.close()


def test_element_api_success_never_certifies_unimplemented_renderer(tmp_path, monkeypatch):
    """Even 141 green semantic contracts are not 141 working UI controls."""
    monkeypatch.setattr(acid, '_run_pytest', lambda *args,**kwargs: acid.result('test','PASS'))
    monkeypatch.setattr(acid, '_run_browser', lambda *args,**kwargs: {
        'status':'PASS', 'checks':[acid.result('per-element-semantic-contracts','PASS')],
        'elements':[{'id':f'element.test.{i}','family':'test',
                     'semanticContractExecution':'PASS',
                     'registrySerialization':'PASS', 'showcasePresence':'PASS',
                     'functionalBehavior':'UNVERIFIED','implementationStatus':'UNKNOWN'}
                    for i in range(141)], 'visuals':[]})
    report = acid.run(acid.ROOT,output=tmp_path,mode='core')
    assert report['coverage']['elements']['semanticContractExecutions'] == 141
    assert report['coverage']['elements']['independentBehaviorProven'] == 0
    assert report['coverage']['independentFunctionalCompleteness'] is False
    assert report['status'] == 'INCOMPLETE'


def test_single_element_semantic_failure_makes_acid_test_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(acid, '_run_pytest', lambda *args,**kwargs: acid.result('suite','PASS'))
    monkeypatch.setattr(acid, '_run_browser', lambda *args,**kwargs: {
        'status':'FAIL', 'checks':[acid.result('per-element-semantic-contracts','FAIL')],
        'elements':[{'id':'element.sabotaged','family':'core',
                     'semanticContractExecution':'FAIL',
                     'registrySerialization':'PASS', 'showcasePresence':'PASS',
                     'functionalBehavior':'UNVERIFIED','implementationStatus':'UNKNOWN'}],
        'visuals':[]})
    report=acid.run(acid.ROOT,output=tmp_path,mode='core')
    assert report['status']=='FAIL'
    assert report['coverage']['elements']['semanticContractFailures']==1
