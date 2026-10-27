"""Chromium acceptance for the reusable MCEL declarative collection runtime.

Uses the compiler-generated IR and byte-identical shared runtime, not a test
renderer. Navigation may be restricted; in that case the shared assets and IR
are mounted into a real Chromium document and the limitation is recorded.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from main_computer.mcel_declarative_collection_package import build, RUNTIME, STYLE


def check(source: Path, output: Path, widths: tuple[int, ...] = (320, 390, 560, 1100)) -> dict[str, Any]:
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    package = output / 'candidate'
    package_result = build(Path(source), package)
    if package_result['status'] != 'BUILT_UNVERIFIED':
        return {'status': 'BLOCKED', 'package': package_result}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {'status': 'BLOCKED', 'reason': 'playwright-not-installed', 'package': package_result}
    from tools.mcel_promise_gap_frob import _chromium
    ir = json.loads((package / 'app.ir.json').read_text(encoding='utf-8'))
    js = (package / RUNTIME).read_text(encoding='utf-8')
    css = (package / STYLE).read_text(encoding='utf-8')
    observations = []
    try:
        with sync_playwright() as p:
            browser = _chromium(p)
            try:
                for width in widths:
                    page = browser.new_page(viewport={'width': width, 'height': 860})
                    errors = []
                    page.on('pageerror', lambda exc: errors.append(str(exc)))
                    try:
                        # The sandbox blocks Chromium loopback navigation. This mounts
                        # the exact same generic runtime JS/CSS and compiled IR.
                        page.set_content('<!doctype html><html><head></head><body><main id="mcel-root"></main>'
                                         '<div id="second-root"></div></body></html>')
                        page.add_style_tag(content=css)
                        page.add_script_tag(content=js)
                        page.evaluate('''ir => {window.mcelMounted = McelCollectionApplication.mount(
                            document.getElementById('mcel-root'), ir); window.mcelSecond =
                            McelCollectionApplication.mount(document.getElementById('second-root'), ir)}''', ir)
                        root = page.locator('#mcel-root')
                        def fill(sku: str, name: str, qty: str, category: str) -> None:
                            for key, val in [('sku',sku),('name',name),('quantity',qty),('category',category)]:
                                root.locator(f'[data-mcel="field-{key}"]').fill(val)
                            root.locator('[data-mcel="create"]').click()
                        def snap() -> list[dict[str, Any]]:
                            return page.evaluate('window.mcelMounted.snapshot()')
                        events = []
                        events.append(('schema-refusal', page.evaluate('''ir => {
                          const rogue = JSON.parse(JSON.stringify(ir));
                          const form = rogue.surfaces.flatMap(x => x.nodes || []).find(x => x.nodeKind === 'form');
                          form.fields.quantity.minimum = -900;
                          const root = document.createElement('main');
                          try {McelCollectionApplication.mount(root, rogue); return false;}
                          catch(e) {return String(e).includes('declared-field-mismatch') && !root.hasAttribute('data-mcel-mounted') && !root.childNodes.length;}
                        }''', ir)))
                        events.append(('undeclared-authority-refusal', page.evaluate('''ir => {
                          const rogue = JSON.parse(JSON.stringify(ir));
                          rogue.effects.find(x => x.effectKind === 'canonical-write').authority.ref = 'state:unauthorized';
                          const root = document.createElement('main');
                          try {McelCollectionApplication.mount(root, rogue); return false;}
                          catch(e) {return String(e).includes('unverified-action:') && !root.hasAttribute('data-mcel-mounted') && !root.childNodes.length;}
                        }''', ir)))
                        events.append(('duplicate-mount-refused', page.evaluate('''ir => {
                          const root = document.querySelector('#mcel-root');
                          try {McelCollectionApplication.mount(root, ir); return false;}
                          catch(e) {return String(e).includes('duplicate-mount') && root.querySelector('[data-mcel="create"]');}
                        }''', ir)))
                        fill('', 'No key', '3', 'Widgets')
                        events.append(('empty-sku-refused', not snap() and 'Refused' in root.locator('[role="status"]').inner_text()))
                        fill('broken', 'Invalid', '-1', 'Widgets')
                        events.append(('negative-quantity-refused', not snap()))
                        fill('A-1', 'Hammer', '3', 'Tools')
                        fill('B-2', 'Saw', '7', 'Tools')
                        events.append(('two-added', len(snap()) == 2 and root.locator('[data-mcel="row"]').count() == 2))
                        fill('A-1', 'Duplicate', '8', 'Tools')
                        events.append(('duplicate-refused', len(snap()) == 2 and snap()[0]['name'] == 'Hammer'))
                        edit = root.locator('[data-mcel="row"][data-key="B-2"] [data-mcel="edit-quantity"]')
                        edit.fill('11')
                        root.locator('[data-mcel="row"][data-key="B-2"] [data-mcel="update"]').click()
                        events.append(('row-bound-update', [(x['sku'],x['quantity']) for x in snap()] == [('A-1',3),('B-2',11)]))
                        edit_name = root.locator('[data-mcel="row"][data-key="B-2"] [data-mcel="edit-name"]')
                        edit_name.fill('Crosscut saw')
                        root.locator('[data-mcel="row"][data-key="B-2"] [data-mcel="update"]').click()
                        events.append(('non-integer-field-update', snap()[1]['name'] == 'Crosscut saw' and snap()[1]['sku'] == 'B-2'))
                        root.locator('[data-mcel="row"][data-key="B-2"] [data-mcel="edit-quantity"]').fill('-9')
                        root.locator('[data-mcel="row"][data-key="B-2"] [data-mcel="update"]').click()
                        events.append(('negative-update-refused', snap()[1]['quantity'] == 11 and
                                       root.locator('[data-mcel="receipt"]').first.get_attribute('data-reason') == 'minimum:item.quantity'))
                        root.locator('[data-mcel="search"]').fill('hammer')
                        events.append(('search-local', root.locator('[data-mcel="row"]').count() == 1 and
                                       root.locator('[data-mcel="row"]').first.get_attribute('data-key') == 'A-1'
                                       and len(snap()) == 2))
                        events.append(('instance-isolation', page.evaluate('window.mcelSecond.snapshot().length') == 0 and
                                       page.locator('#second-root [data-mcel="search"]').input_value() == ''))
                        root.locator('[data-mcel="search"]').fill('')
                        root.locator('[data-mcel="sort"]').select_option('quantity')
                        events.append(('sort-local', [x.get_attribute('data-key') for x in
                                       root.locator('[data-mcel="row"]').all()] == ['A-1','B-2']))
                        page.evaluate('''() => {window.stale = document.querySelector(
                            '#mcel-root [data-mcel="row"][data-key="B-2"] [data-mcel="remove"]')}''')
                        root.locator('[data-mcel="row"][data-key="B-2"] [data-mcel="remove"]').click()
                        events.append(('remove-identity', len(snap()) == 1 and snap()[0]['sku'] == 'A-1'))
                        page.evaluate('window.stale.click()')
                        events.append(('stale-target-refused', len(snap()) == 1 and
                                       root.locator('[data-mcel="receipt"]').first.get_attribute('data-reason') == 'missing-row:B-2'))
                        receipts = page.evaluate('window.mcelMounted.receipts()')
                        completed = [x for x in receipts if x['disposition'] == 'completed']
                        refused = [x for x in receipts if x['disposition'] == 'refused-before-attempt']
                        events.append(('receipt-reconciliation', len(completed) == 5 and len(refused) == 5 and
                                       all(x['before'] == x['after'] for x in refused) and
                                       all(x['before'] != x['after'] for x in completed) and
                                       [x['sequence'] for x in receipts] == list(range(1,len(receipts)+1))))
                        events.append(('runtime-state', len(errors) == 0))
                        page.locator('#second-root').evaluate('(el) => el.remove()')
                        geometry = page.evaluate('''() => ({viewport: innerWidth,
                            documentWidth: document.documentElement.scrollWidth,
                            rootWidth: document.getElementById('mcel-root').getBoundingClientRect().width})''')
                        events.append(('contained', geometry['documentWidth'] <= width + 1))
                        shot = output / f'inventory-{width}.png'
                        page.screenshot(path=str(shot),full_page=True)
                        observations.append({'width':width,'status':'PASS' if all(ok for _,ok in events) else 'FAIL',
                          'checks': [{'name':name,'ok':ok} for name,ok in events],
                          'receiptCount':len(receipts),'successfulMutations':len(completed),
                          'refusals':len(refused),'geometry':geometry,'errors':errors,'screenshot':shot.name})
                    finally:
                        page.close()
            finally:
                browser.close()
    except Exception as exc:
        return {'status':'BLOCKED','reason':f'{type(exc).__name__}: {exc}', 'package':package_result,
                'observations':observations}
    return {'status':'PASS' if observations and all(x['status']=='PASS' for x in observations) else 'FAIL',
            'package':package_result,'observations':observations,
            'browserMode':'real-chromium-dom-mount; local-navigation-unavailable',
            'limitations':['volatile in-memory canonical state only',
              'receipt objects observed in same browser process, not independent persisted receipts',
              'does not establish normal host application route integration',
              'no aesthetics certification', 'Work Orders remains an independent unimplemented task']}


if __name__ == '__main__':
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,default=Path('tools/fixtures/mcel_promise_gap/inventory_application.js'))
    p.add_argument('--output',type=Path,default=Path('runtime/reports/mcel-promise-gap/inventory-browser'))
    args=p.parse_args()
    report=check(args.source,args.output)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'acceptance.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('MCEL_COLLECTION_BROWSER_'+report['status'])
    for observation in report.get('observations',[]):
        print('width',observation['width'],observation['status'],
              [x['name'] for x in observation['checks'] if not x['ok']])
    if report['status']!='PASS': print(report.get('reason',''))
    raise SystemExit(0 if report['status']=='PASS' else 1)
