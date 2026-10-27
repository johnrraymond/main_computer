"""Patch 2.1: browser-gated MCEL representation -> layout Frob.

Run: python tools/mcel_representation_layout_frob.py
     python tools/mcel_representation_layout_frob.py --no-browser  # UNVERIFIED

The first fixture preserves Patch 2's negative controls. Four independent
flat-file datasets challenge identifier, missingness, and allocation integrity.
No production applications, website builder, or databases are touched.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from zipfile import ZipFile, ZIP_DEFLATED

try:
    from tools.mcel_text_shrinker_adapter import load_flat_file
    from tools.mcel_representation_layout_contract import project_layout, page_for_layout
except ModuleNotFoundError:
    from mcel_text_shrinker_adapter import load_flat_file
    from mcel_representation_layout_contract import project_layout, page_for_layout

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT/'tools/fixtures/mcel_text_shrinker_widgets.json'
ADVERSARIAL_FIXTURE = ROOT/'tools/fixtures/mcel_representation_layout_adversarial.json'
OUTPUT = ROOT/'runtime/reports/mcel-representation-layout'
WIDTHS = (320, 390, 560, 640, 1100)
WIDGETS = ('downloads', 'inventory')
ADVERSARIAL_WIDGETS = ('long_identifiers', 'near_collisions', 'missing_fields', 'competing_required')
ADVERSARIAL_WIDTHS = (560, 640)


def _check(ok, reason):
    if not ok:
        raise AssertionError(reason)


def _inspector_geometry(page, name):
    """Exact-value legibility must not rely on invisible horizontal clipping."""
    geometry = page.evaluate("""() => {
      const inspector = document.querySelector('#mcel-inspector');
      const r = inspector.getBoundingClientRect();
      const paragraphs = [...inspector.querySelectorAll('p')].map(p => {
        const rect = p.getBoundingClientRect();
        const range = document.createRange(); range.selectNodeContents(p);
        const glyphs = [...range.getClientRects()];
        return {width:p.clientWidth, scrollWidth:p.scrollWidth,
          x:rect.left, right:rect.right,
          glyphRight:glyphs.length ? Math.max(...glyphs.map(g=>g.right)) : rect.right};
      });
      return {x:r.left,right:r.right,scrollWidth:inspector.scrollWidth,
        clientWidth:inspector.clientWidth, paragraphs,
        horizontal_document_overflow:document.documentElement.scrollWidth > innerWidth+1};
    }""")
    vw=page.viewport_size['width']
    _check(geometry['x'] >= -1 and geometry['right'] <= vw+1,
           f'{name}: inspector container extends outside viewport: {geometry}')
    _check(geometry['scrollWidth'] <= geometry['clientWidth']+1,
           f'{name}: inspector horizontally overflows: {geometry}')
    _check(not geometry['horizontal_document_overflow'], f'{name}: inspector causes document overflow')
    _check(geometry['paragraphs'], f'{name}: exact-value inspector has no details')
    for item in geometry['paragraphs']:
        _check(item['scrollWidth'] <= item['width']+1 and item['x'] >= -1 and
               item['right'] <= vw+1 and item['glyphRight'] <= vw+1,
               f'{name}: exact original clipped rather than wrapped: {item}')
    return {'status':'PASS','paragraph_count':len(geometry['paragraphs']),
            'max_paragraph_scroll_excess_px':max((x['scrollWidth']-x['width']
                for x in geometry['paragraphs']), default=0)}


def _assert_identity(page, doc, name):
    rows=doc['source_rows']; columns=doc['field_contracts']
    fields=page.evaluate("""() => [...document.querySelectorAll('mcel-field')].map(f => ({
      row:f.dataset.sourceRowId, key:f.dataset.semanticField, full:f.dataset.full,
      instance:f.dataset.semanticInstanceId, element:f.dataset.mcelElementId,
      missing:f.dataset.sourceMissing === 'true', empty:f.dataset.sourceEmpty === 'true',
      shown:f.querySelector('.field-value').textContent
    }))""")
    expected={(str(r['id']), c['key']): str(r.get(c['key'],'')) for r in rows for c in columns}
    actual={(f['row'],f['key']):f['full'] for f in fields}
    _check(len(fields)==len(expected) and actual==expected,
           f'{name}: source identity/raw values changed')
    _check(all(f['element']=='element.core.field' and f['instance'] for f in fields),
           f'{name}: lost registered field identity')
    by_cell={(f['row'], f['key']):f for f in fields}
    for r in rows:
        for col in columns:
            key=col['key']; f=by_cell[(str(r['id']),key)]
            missing=key not in r
            empty=not missing and r[key]==''
            _check(f['missing']==missing and f['empty']==empty,
                   f'{name}: missing/empty conflated at {r["id"]}/{key}')
            if missing: _check(f['shown']=='Not provided',f'{name}: missing marker absent')
            if empty: _check(f['shown']=='Empty',f'{name}: empty marker absent')
    for col in columns:
        if col.get('kind') != 'identifier': continue
        pairs=[(str(r.get(col['key'],'')),by_cell[(str(r['id']),col['key'])]['shown']) for r in rows]
        for i,(raw_a,shown_a) in enumerate(pairs):
            for raw_b,shown_b in pairs[i+1:]:
                _check(raw_a==raw_b or shown_a!=shown_b,
                       f'{name}: distinct identifiers collide: {raw_a!r} / {raw_b!r}')
    # Inspect first, explicitly empty, and longest row independently. This
    # tests the original 320px issue, rather than only global document scroll.
    examine=[rows[0]]
    empties=[r for r in rows if any(c['key'] in r and r[c['key']]=='' for c in columns)]
    if empties and empties[0] not in examine:examine.append(empties[0])
    longest=max(rows,key=lambda r:max((len(str(r.get(c['key'],''))) for c in columns),default=0))
    if longest not in examine:examine.append(longest)
    inspectors=[]
    for r in examine:
        rid=str(r['id'])
        page.locator('mcel-row').evaluate_all("""(nodes, rid) => {
          const row=nodes.find(n=>n.dataset.rowId===rid);
          if(!row)throw Error('Missing row '+rid);
          row.click();
        }""", rid)
        inspector=page.locator('#mcel-inspector').inner_text()
        for col in columns:
            k=col['key']
            expected_text='[not provided]' if k not in r else '[empty]' if r[k]=='' else str(r[k])
            _check(expected_text in inspector,
                   f'{name}: exact original/status {rid}/{k} not inspectable')
        inspectors.append(_inspector_geometry(page,name+'-'+rid))
    sort_key=doc['behavior']['sort_by']
    page.evaluate('(k) => window.__MCEL_INFORMATION_FROB.sortBy(k)',sort_key)
    observed=page.evaluate('() => window.__MCEL_INFORMATION_FROB.rows')
    sorted_rows=sorted(rows,key=lambda r:(r.get(sort_key,''),str(r['id'])))
    _check(observed==[str(r['id']) for r in sorted_rows],f'{name}: raw typed sort changed')
    identifier=next((c for c in columns if c.get('kind')=='identifier'),columns[0])['key']
    first=str(rows[0]['id'])
    raw=str(rows[0].get(identifier,''))
    found=page.evaluate('(q) => window.__MCEL_INFORMATION_FROB.filter(q)',raw)
    _check(found==[first],f'{name}: exact raw identifier search failed: {found}')
    page.evaluate('() => window.__MCEL_INFORMATION_FROB.filter("")')
    page.evaluate('() => window.__MCEL_REPRESENTATION_LAYOUT.apply()')
    return {'raw_values':'PASS','registered_identity':'PASS',
            'pairwise_identifier_distinctions':'PASS','missing_vs_empty':'PASS',
            'exact_inspector_no_overflow':'PASS','inspector_checks':inspectors,
            'raw_search':'PASS','typed_sort':'PASS'}


def _browser_proof(out,projections,fixture,adversarial_fixture,*,chromium_executable=None):
    from playwright.sync_api import sync_playwright
    try:
        from tools.mcel_browser_launch import launch_chromium
    except ModuleNotFoundError:
        from mcel_browser_launch import launch_chromium
    checks={}
    with sync_playwright() as p:
        browser,browser_details=launch_chromium(p,executable=chromium_executable,headless=True)
        try:
            for family,widget,width,before,after in projections:
                for mode,doc in (('before',before),('after',after)):
                    name=(f'{widget}-{width}-{mode}' if family=='baseline' else
                          f'{family}-{widget}-{width}-{mode}')
                    page=browser.new_page(viewport={'width':width,'height':800},device_scale_factor=1)
                    errors=[]
                    page.on('pageerror',lambda err,dest=errors:dest.append(str(err)))
                    try:
                        page.set_content((out/f'{name}.html').read_text(encoding='utf-8'),wait_until='load')
                        page.wait_for_function('!!window.__MCEL_REPRESENTATION_LAYOUT')
                        measures=page.evaluate('() => window.__MCEL_REPRESENTATION_LAYOUT.measure()')
                        _check(measures['layout_authority']=='mcel-semantic-layout-inference.v1' and
                               measures['engine_schema']=='mcel.semantic-collection-layout-evaluation.v1',
                               f'{name}: shared application layout engine did not authorize layout')
                        _check(measures['rows']==len(doc['source_rows']),f'{name}: row count mismatch')
                        _check(measures['registered_component_tags']==['element.core.field'],
                               f'{name}: not registered elements')
                        _check(not measures['horizontal_document_overflow'],f'{name}: document overflow')
                        _check(measures['mode']==('stacked' if width<=390 else 'table'),
                               f'{name}: unexpected responsive mode')
                        if mode=='after':
                            _check(measures['compressed_cell_count']>0,f'{name}: no compression')
                            _check(measures['all_required_visible'],
                                   f'{name}: required fields clip: {measures["required_unsatisfied_keys"]}')
                        else:
                            _check(measures['compressed_cell_count']==0,f'{name}: opt-out changed presentation')
                        identity=_assert_identity(page,doc,name)
                        _check(not page.evaluate('document.documentElement.scrollWidth>innerWidth+1'),
                               f'{name}: inspector introduces overflow')
                        page.screenshot(path=str(out/f'{name}.png'),full_page=True)
                        _check(not errors,f'{name}: browser error: {errors}')
                        checks[name]={'status':'PASS','geometry':measures,'identity':identity,'errors':errors}
                    finally:
                        page.close()
            for width in ADVERSARIAL_WIDTHS:
                off=checks[f'downloads-{width}-before']['geometry']
                on=checks[f'downloads-{width}-after']['geometry']
                lost=[k for k,v in off['required_visible'].items() if not v]
                recovered=off['intrinsic_px']-on['intrinsic_px']
                gained={k:on['allocated_px'][k]-off['allocated_px'][k]
                        for k in on['required_visible'] if k!='name'}
                _check(lost,f'Negative control at {width}: no required field lost with shrinker off')
                _check(on['all_required_visible'],f'Negative control at {width}: no repair')
                _check(recovered>0 and any(v>0 for v in gained.values()),
                       f'Negative control at {width}: recovered pixels were not reallocated')
                checks[f'adversarial-{width}']={'status':'PASS',
                    'required_lost_without_shrinker':lost,
                    'recovered_intrinsic_px':recovered,
                    'required_field_width_gains_px':gained,
                    'required_restored_with_shrinker':True}
            for widget in ADVERSARIAL_WIDGETS:
                for width in WIDTHS:
                    key=f'adversarial-{widget}-{width}'
                    off=checks[key+'-before']['geometry']
                    on=checks[key+'-after']['geometry']
                    _check(off['compressed_cell_count']==0 and on['compressed_cell_count']>0,
                           f'{key}: no genuine compression')
                    _check(on['intrinsic_px']<off['intrinsic_px'],f'{key}: no recovered intrinsic space')
                    _check(on['all_required_visible'],f'{key}: clipped required fields')
                    _check(on['missing_cell_count']==off['missing_cell_count'] and
                           on['empty_cell_count']==off['empty_cell_count'],
                           f'{key}: missing/empty status changed after compression')
                    if widget=='missing_fields':
                        _check(on['missing_cell_count']>=1 and on['empty_cell_count']>=1,
                               f'{key}: absence/empty controls missing')
                    if widget=='competing_required':
                        _check(len(on['required_visible'])>=5 and all(on['required_visible'].values()),
                               f'{key}: competing required fields not visible')
                    checks[key]={'status':'PASS','intrinsic_recovered_px':off['intrinsic_px']-on['intrinsic_px'],
                                 'required_visible':on['required_visible'],
                                 'missing_count':on['missing_cell_count'],
                                 'explicitly_empty_count':on['empty_cell_count']}
            # The shared application engine is a hard dependency: removing it
            # must refuse initialization, not fall back to local Frob sizing.
            authority_script=(ROOT/'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js').read_text(encoding='utf-8')
            engine_negative=browser.new_page(viewport={'width':560,'height':800})
            try:
                candidate=(out/'downloads-560-after.html').read_text(encoding='utf-8')
                _check(candidate.count('<script>'+authority_script+'</script>')==1,
                       'Engine negative control cannot isolate the shared layout script')
                denied=[]
                engine_negative.on('pageerror',lambda error:denied.append(str(error)))
                engine_negative.set_content(candidate.replace('<script>'+authority_script+'</script>',''),
                                            wait_until='load')
                _check(any('MCEL_SHARED_LAYOUT_AUTHORITY_UNAVAILABLE' in error for error in denied) and
                       not engine_negative.evaluate('!!window.__MCEL_REPRESENTATION_LAYOUT'),
                       'Missing shared layout engine silently fell back to Frob planner')
                checks['shared-layout-engine-missing-negative-control']={
                    'status':'PASS','refused_without_engine':True,'fallback_unavailable':True}
            finally:engine_negative.close()
            # A deliberate CSS regression must make the inspector fail: this
            # proves the inspector-fit assertion detects, rather than ignores,
            # a long unbroken original at 320px.
            negative=browser.new_page(viewport={'width':320,'height':800})
            try:
                candidate=out/'adversarial-long_identifiers-320-after.html'
                negative.set_content(candidate.read_text(encoding='utf-8'),wait_until='load')
                negative.locator('mcel-row').first.click()
                _inspector_geometry(negative,'inspector-negative-preflight')
                negative.evaluate("""() => {
                  const style=document.createElement('style');
                  style.id='regression-no-inspector-wrap';
                  style.textContent='#mcel-inspector p { white-space:nowrap !important; '
                    +'overflow-wrap:normal !important; word-break:normal !important; }';
                  document.head.append(style);
                }""")
                rejected=False
                try:_inspector_geometry(negative,'inspector-negative-disabled')
                except AssertionError:rejected=True
                _check(rejected,'Inspector-width negative control failed to detect missing wrapping')
                negative.evaluate("""() => document.querySelector('#regression-no-inspector-wrap').remove()""")
                repaired=_inspector_geometry(negative,'inspector-negative-repaired')
                checks['inspector-nowrap-negative-control']={
                    'status':'PASS','unwrapped_original_detected_as_failure':True,
                    'repaired_inspector_visible':repaired['status']=='PASS'}
            finally:negative.close()
            # Verify reflows on the same DOM (without rebuilding source or remounting).
            for family,source,widget in (
                ('baseline',fixture,'downloads'),
                ('adversarial',adversarial_fixture,'long_identifiers'),
                ('adversarial',adversarial_fixture,'missing_fields')):
                demo=project_layout(source['widgets'][widget],source['value_sets'],
                                    viewport_px=560,enabled=True)
                page=browser.new_page(viewport={'width':560,'height':800})
                try:
                    page.set_content(page_for_layout(demo,widget,'responsive'),wait_until='load')
                    timeline=[]
                    for width in (560,390,320,1100,560):
                        page.set_viewport_size({'width':width,'height':800})
                        page.wait_for_function('!!window.__MCEL_REPRESENTATION_LAYOUT')
                        page.evaluate('() => window.__MCEL_REPRESENTATION_LAYOUT.apply()')
                        value=page.evaluate('() => window.__MCEL_REPRESENTATION_LAYOUT.measure()')
                        _check(value['all_required_visible'] and not value['horizontal_document_overflow'],
                               f'{family}/{widget}: live resize failed at {width}')
                        page.locator('mcel-row').first.click()
                        inspect=_inspector_geometry(page,f'{family}/{widget}-resize-{width}')
                        timeline.append({'width':width,'layout':value['mode'],
                                         'required_visible':value['required_visible'],
                                         'inspector':inspect['status']})
                    _check([v['layout'] for v in timeline]==
                           ['table','stacked','stacked','table','table'],
                           f'{family}/{widget}: responsive transitions failed')
                    checks[f'{family}-{widget}-live-resize']={'status':'PASS','timeline':timeline}
                finally:page.close()
        finally:browser.close()
    return {'browser':browser_details,'checks':checks}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',type=Path,default=FIXTURE)
    parser.add_argument('--adversarial-fixture',type=Path,default=ADVERSARIAL_FIXTURE)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--chromium-executable')
    args=parser.parse_args(argv)
    fixture=load_flat_file(args.fixture)
    adversarial_fixture=load_flat_file(args.adversarial_fixture)
    _check(set(ADVERSARIAL_WIDGETS).issubset(adversarial_fixture['widgets']),
           'Independent adversarial fixture is incomplete')
    _check(not set(fixture['value_sets']).intersection(adversarial_fixture['value_sets']),
           'Independent semantic context overlaps baseline fixture')
    _check(not set(WIDGETS).intersection(adversarial_fixture['widgets']),
           'Independent widgets overlap baseline fixture')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    projections=[]
    for family,source,names in (
        ('baseline',fixture,WIDGETS),('adversarial',adversarial_fixture,ADVERSARIAL_WIDGETS)):
        for widget in names:
            for width in WIDTHS:
                before=project_layout(source['widgets'][widget],source['value_sets'],
                                      viewport_px=width,enabled=False)
                after=project_layout(source['widgets'][widget],source['value_sets'],
                                     viewport_px=width,enabled=True)
                projections.append((family,widget,width,before,after))
                for mode,data in (('before',before),('after',after)):
                    name=(f'{widget}-{width}-{mode}' if family=='baseline' else
                          f'{family}-{widget}-{width}-{mode}')
                    (out/f'{name}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                    (out/f'{name}.html').write_text(page_for_layout(data,widget,mode),encoding='utf-8')
    proof=None;error=None
    if not args.no_browser:
        try:proof=_browser_proof(out,projections,fixture,adversarial_fixture,
                                 chromium_executable=args.chromium_executable)
        except Exception as exc:error=f'{type(exc).__name__}: {exc}'
    status='FAIL' if error else 'UNVERIFIED' if args.no_browser else 'PASS'
    summary={'schema':'mcel.representation-layout-frob.v2','status':status,
       'evidence':proof,'error':error,'fixture':str(args.fixture),
       'adversarial_fixture':str(args.adversarial_fixture),
       'widgets':WIDGETS+ADVERSARIAL_WIDGETS,'independent_datasets':ADVERSARIAL_WIDGETS,
       'viewports':WIDTHS,'adversarial_viewports':ADVERSARIAL_WIDTHS,
       'requirements':['space allocation','contextual compression','identifier pairwise uniqueness',
                       'exact inspector horizontal fit','absent versus explicitly empty fields',
                       'responsive layout including live resize',
                       'independent long identifiers, near collisions and competing required fields',
                       'shrink-disabled negative controls',
                       'shared layout engine authority, absent-engine refusal and no local fallback'],
       'shared_engine_integrated':True,
       'production_integrated':False,
       'limitations':['Shared layout engine integrated by explicit collection opt-in; no production application route mounted',
                       'Browser text metrics vary by browser and fonts',
                       'Screen-reader experience and subjective design quality not certified']}
    (out/'report.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    sections=[]
    for family,widget,width,_,_ in projections:
        for mode in ('before','after'):
            name=(f'{widget}-{width}-{mode}' if family=='baseline' else
                  f'{family}-{widget}-{width}-{mode}')
            if (out/f'{name}.png').is_file() and proof:
                sections.append(f'<section><h3>{name}</h3><img src="{name}.png" alt="{name}"></section>')
            else:
                sections.append(f'<section><a href="{name}.html">{name}</a></section>')
    gallery=('<!doctype html><html lang="en"><meta charset="utf-8">'
        '<title>Representation to layout Frob</title><style>body{font:14px system-ui;margin:22px;'
        'background:#f4f7fb}section{background:#fff;margin:10px;padding:12px}img{max-width:100%;'
        'height:auto;border:1px solid #ccd}a{color:#145}</style><h1>MCEL representation to layout</h1>'
        f'<p>Status: {status}. Browser measurements in report.json.</p>'+''.join(sections)+'</html>')
    (out/'gallery.html').write_text(gallery,encoding='utf-8')
    with ZipFile(out/'representation-layout-review.zip','w',ZIP_DEFLATED) as z:
        for path in sorted(out.iterdir()):
            if path.is_file() and path.suffix in ('.html','.json','.png'):z.write(path,path.name)
    print('MCEL_REPRESENTATION_LAYOUT_FROB_'+status)
    print('widgets:',len(WIDGETS)+len(ADVERSARIAL_WIDGETS),'viewports:',WIDTHS,
          'negative controls:',ADVERSARIAL_WIDTHS)
    if proof:
        for width in ADVERSARIAL_WIDTHS:
            print('negative control',width,proof['checks'][f'adversarial-{width}'])
    if error: print('ERROR:',error)
    print('report:',out/'report.json');print('gallery:',out/'gallery.html')
    print('review:',out/'representation-layout-review.zip')
    return 1 if error else 0

if __name__=='__main__':sys.exit(main())
