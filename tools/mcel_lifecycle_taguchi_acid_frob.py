"""Read-only MCEL Lab L18 lifecycle acid acceptance using the actual universal renderer.

    python -m tools.mcel_lifecycle_taguchi_acid_frob

The browser mount is an offline composition of the current Task Manager semantic
fixture and the real MCEL Lab lifecycle acid workspace. This does not exercise
production Task Manager, virtualization, or concurrent browser-worker schedules.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
from tools.mcel_patch14_7_capability_negotiation_frob import SCRIPTS, _chrome

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "main_computer/web/applications/apps/mcel-lab.html"
CSS = ROOT / "main_computer/web/applications/styles/mcel-lab.css"
OUTPUT = ROOT / "runtime/reports/mcel-lifecycle-taguchi-acid"
FILES = (
    'mcel-presentation-negotiation.js', 'mcel-presentation-hosts.js',
    'mcel-layout-continuity.js', 'mcel-universal-semantic-layout-lab.js',
    'mcel-lifecycle-acid-fixtures.js', 'mcel-lifecycle-acid-lab.js'
)
SCHEMA = 'mcel.lifecycle-taguchi-acid-browser-acceptance.v1'


def fragment() -> str:
    source = APP.read_text(encoding='utf-8')
    start = source.index('  <section id="mcel-lifecycle-acid"')
    finish = source.index('  <section\n    class="mcel-element-acid"', start)
    return source[start:finish]


def install(page) -> None:
    page.set_content(html_fixture(), wait_until='load')
    page.add_style_tag(content=CSS.read_text(encoding='utf-8'))
    for filename in FILES:
        page.add_script_tag(path=str(SCRIPTS / filename))
    page.evaluate("html=>{document.body.insertAdjacentHTML('beforeend',html);McelLifecycleAcidLab.init()}", fragment())


def verify(output: Path=OUTPUT, widths: tuple[int, ...]=(390, 1440), *, seed: int=1475, repetitions: int=2) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    report = {'schema': SCHEMA, 'generatedUtc': datetime.now(timezone.utc).isoformat(),
              'readOnly': True, 'productionPromotion': False,
              'operatorActionsExecuted': False, 'cases': [], 'errors': [],
              'limitations': ['Offline read-only Task Manager semantic fixture',
                              'L18 main-effect screening; interaction effects are confounded',
                              'No virtualized host, cross-browser certification, true concurrency or real user timing']}
    with sync_playwright() as p:
        browser = _chrome(p, None)
        try:
            for width in widths:
                page = browser.new_page(viewport={'width': width, 'height': 950}, reduced_motion='reduce')
                errors: list[str] = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                try:
                    install(page)
                    assert page.locator('#mcel-lifecycle-acid').get_attribute('data-ready') == 'true'
                    page.locator('.mcel-lifecycle-acid__automated summary').click()
                    page.locator('#mcel-lifecycle-acid-seed').fill(str(seed))
                    page.locator('#mcel-lifecycle-acid-repetitions').fill(str(repetitions))
                    page.locator('#mcel-lifecycle-acid-screen').click()
                    page.wait_for_function("document.querySelector('#mcel-lifecycle-acid-overview').textContent.includes('18/18')", timeout=120000)
                    matrix = page.evaluate("() => JSON.parse(document.querySelector('#mcel-lifecycle-acid-trace').textContent)")
                    all_pass = len(matrix['runs']) == 18 and all(
                        run['verdict'] == 'pass' and len(run['replicas']) == repetitions and
                        all(rep['verdict'] == 'pass' and all(rep['checks'].values()) for rep in run['replicas'])
                        for run in matrix['runs'])
                    # Negative control: an invalid semantic collection must be rejected
                    # by the real composer, and the acid oracle must call it FAIL.
                    negative_control = page.evaluate('''async()=>{
                      const previous=McelLifecycleAcidFixtures;
                      const invalid=structuredClone(previous.normal);
                      invalid.collection.noOperations=false;
                      window.McelLifecycleAcidFixtures={...previous,normal:invalid};
                      const stage=document.createElement('div');document.body.append(stage);
                      try{const outcome=await McelLifecycleAcidLab.runOne(McelLifecycleAcidLab.EXPERIMENTS[0].settings,{stage});
                        return {detected:outcome.verdict==='fail',reason:outcome.failure};}
                      finally{window.McelLifecycleAcidFixtures=previous;stage.remove();}
                    }''')
                    page.locator('#mcel-lifecycle-acid-matrix tr[data-run="L18-05"] button').click()
                    page.locator('#mcel-lifecycle-acid-focused').click()
                    page.wait_for_function("document.querySelectorAll('#mcel-lifecycle-acid-preview mcel-row').length>0")
                    focus_rows = page.locator('#mcel-lifecycle-acid-preview mcel-row').count()
                    page.locator('[data-mcel-acid-action="fault injection"]').click()
                    fault = page.evaluate("() => JSON.parse(document.querySelector('#mcel-lifecycle-acid-trace').textContent)")
                    page.locator('[data-mcel-acid-action="native selection"]').click()
                    selection = page.evaluate("() => JSON.parse(document.querySelector('#mcel-lifecycle-acid-trace').textContent)")
                    image_path = output / f'lifecycle-acid-{width}.png'
                    page.locator('#mcel-lifecycle-acid').screenshot(path=str(image_path), timeout=30000)
                    case = {'viewport': width, 'status': 'PASS' if all_pass and focus_rows and
                        fault.get('result',{}).get('ok') and selection.get('result',{}).get('ok') and negative_control['detected'] and not errors else 'FAIL',
                        'runs':matrix['runs'], 'orthogonal':matrix['orthogonal'], 'seed':matrix['seed'],
                        'repetitions':matrix['repetitions'], 'focusedRows':focus_rows,
                        'rollbackInjected':fault.get('result'), 'nativeSelection':selection.get('result'),
                        'negativeControl':negative_control,
                        'browserErrors':errors, 'screenshot':str(image_path)}
                    report['cases'].append(case)
                    if case['status'] != 'PASS': report['errors'].append(f'Acceptance failed at {width}px')
                except Exception as error:
                    report['errors'].append(f'{width}px: {type(error).__name__}: {error}')
                finally: page.close()
        finally: browser.close()
    report['status'] = 'PASS' if len(report['cases']) == len(widths) and not report['errors'] else 'FAIL'
    (output / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return report


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--seed', type=int, default=1475)
    parser.add_argument('--repetitions', type=int, default=2, choices=range(1,6))
    opts=parser.parse_args()
    result=verify(opts.output, seed=opts.seed, repetitions=opts.repetitions)
    print('MCEL_LIFECYCLE_TAGUCHI_ACID_'+result['status'])
    for case in result['cases']:
        print(f"{case['viewport']}px {case['status']} {len(case['runs'])} L18 cases x {case['repetitions']} trials")
    for error in result['errors']: print('ERROR:', error)
    print('report:', opts.output / 'report.json')
    return 0 if result['status']=='PASS' else 1

if __name__=='__main__':
    raise SystemExit(main())
