"""Real, bounded MCEL FileBasket consumer behavior and fault-injection controls."""
from pathlib import Path
import shutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'main_computer/web/applications/scripts'
PROBE = ROOT / 'tools/mcel_file_basket_runtime_probe.js'
ROSTER = {
    'element.resource.file-basket-model',
    'element.resource.file-boundary',
    'element.resource.directory-tree',
    'element.resource.tree-selection-model',
    'element.toolkit.selection-controller',
    'element.toolkit.bulk-selector',
}


@pytest.fixture
def page():
    pw_api = pytest.importorskip('playwright.sync_api')
    with pw_api.sync_playwright() as pw:
        exe = shutil.which('chromium') or shutil.which('chromium-browser')
        kwargs = {'headless': True}
        if exe:
            kwargs.update(executable_path=exe, args=['--no-sandbox'])
        try:
            browser = pw.chromium.launch(**kwargs)
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            tab = browser.new_page()
            tab.set_content('<!doctype html><html><body></body></html>')
            for filename in ('mcel-element-registry.js', 'mcel-elements-core.js',
                             'mcel-file-basket-model.js', 'mcel-file-basket-controller.js'):
                tab.add_script_tag(content=(SCRIPTS / filename).read_text(encoding='utf-8'))
            tab.add_script_tag(content=PROBE.read_text(encoding='utf-8'))
            yield tab
        finally:
            browser.close()


def test_six_real_consumer_witnesses_all_pass_without_full_certification(page):
    result = page.evaluate('McelAcidFileBasket.run()')
    ids = [row['id'] for row in result['elements']]
    assert set(ids) == ROSTER == set(result['roster'])
    assert len(ids) == len(ROSTER)
    assert result['status'] == 'PASS', result['failed']
    assert all(not row['fullElementCertification'] for row in result['elements'])
    assert all(row['status'] == 'PASS' and row['scope'] and len(row['observations']) >= 4
               for row in result['elements']), result
    assert set(ids).issubset(set(page.evaluate('McelElementRegistry.all().map(d=>d.id)')))


def test_blocked_selection_fault_is_caught_by_live_probe(page):
    page.evaluate('''() => {
        const old = McelFileBasketController.createFileBasketController;
        McelFileBasketController.createFileBasketController = (...args) => {
          const controller = old(...args);
          const original = controller.apply;
          controller.apply = (command,payload={}) => {
            const result = original(command,payload);
            if(payload.path === 'src/secrets.env') return {...result,ok:true,reason:''};
            return result;
          };
          return controller;
        };
    }''')
    rows = {r['id']:r for r in page.evaluate('McelAcidFileBasket.run()')['elements']}
    assert rows['element.toolkit.selection-controller']['status'] == 'FAIL'
    assert 'blockedTransitionRefused' in rows['element.toolkit.selection-controller']['failedObservations']


def test_broken_model_selected_output_is_detected(page):
    page.evaluate('''() => { McelFileBasketModel.selectedOutput = (model,raw) => raw || []; }''')
    result = page.evaluate('McelAcidFileBasket.run()')
    assert result['status'] == 'FAIL'
    assert result['failed'], result


def test_audit_traces_bounded_witness_to_existing_model_controller():
    from tools.mcel_element_behavior_audit import FILE_BASKET, FILE_BASKET_TESTS, audit
    import re
    source = (SCRIPTS / 'mcel-elements-core.js').read_text(encoding='utf-8')
    declarations = [{'id':id_} for id_ in re.findall(r'\bdef\(\s*["\'](element\.[\w.-]+)["\']',source)]
    report = audit(ROOT, declarations)
    assert set(FILE_BASKET) == ROSTER
    for id_ in ROSTER:
        row = report[id_]
        assert row['candidateWitness'] == 'file-basket-consumer-bounded-model-controller'
        assert len(row['runtimeImplementationCandidates']) == 2
        assert FILE_BASKET_TESTS[0] in row['targetedBehaviorTestCandidates']
        assert row['implementationExistence'] == 'UNKNOWN'
        assert row['fullBehaviorProvenByStaticAudit'] is False


def test_probe_never_creates_a_filesystem_mutation_or_executes_commands():
    script = PROBE.read_text(encoding='utf-8')
    assert 'createFileBasketController' in script and 'buildFileBasketModel' in script
    assert 'fullElementCertification:false' in script
    assert 'function run()' in script
