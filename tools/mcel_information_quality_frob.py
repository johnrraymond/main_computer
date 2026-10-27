"""MCEL information quality Frob: compare semantic opt-in in actual registered collection elements.

python tools/mcel_information_quality_frob.py [--output PATH] [--no-browser]

The test is intentionally independent of production applications. Screenshots are
research evidence, not subjective certification. No server or database required.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import sys
from zipfile import ZipFile, ZIP_DEFLATED

try:
    from tools.mcel_text_shrinker_adapter import load_flat_file
    from tools.mcel_semantic_information_contract import project_collection
except ModuleNotFoundError:
    from mcel_text_shrinker_adapter import load_flat_file
    from mcel_semantic_information_contract import project_collection

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE=ROOT/'tools/fixtures/mcel_text_shrinker_widgets.json'
DEFAULT_OUTPUT=ROOT/'runtime/reports/mcel-information-quality'
SCRIPTS=ROOT/'main_computer/web/applications/scripts'
CSS="""
*{box-sizing:border-box}body{margin:0;padding:12px;background:#f4f7fb;color:#172536;font:14px system-ui}
header{display:flex;gap:15px;align-items:center;flex-wrap:wrap}h1{font-size:19px}
.toolbar{display:flex;gap:8px;margin:10px 0}input,select{padding:8px;font:inherit;min-width:0}
main{min-width:0}mcel-collection{display:block;min-width:0;width:100%;}
mcel-row{display:grid;grid-template-columns:VAR_TRACKS;gap:7px;align-items:baseline;
  padding:9px;border-bottom:1px solid #dae3eb;background:white;min-width:0;cursor:pointer}
mcel-row:hover,mcel-row:focus{background:#e6f2ff}
mcel-field{display:block;min-width:0;max-width:100%;overflow-wrap:anywhere}
.field-label{display:block;font-size:10px;color:#687d90}.field-value{display:block;overflow-wrap:anywhere}
#panel{border:1px solid #dae3eb;border-radius:8px;overflow:hidden;min-width:0}#mcel-inspector{padding:8px;font-size:12px}
#mode{font-size:12px;color:#667b8e}
"""

def _safe_json(data):
    return json.dumps(data, ensure_ascii=False, separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028')


def page_for(data,widget,mode):
    cols=data['field_contracts']
    widths=data['projection']['column_widths_px']
    if data['projection']['layout']=='stacked':
        tracks='minmax(0,1fr)'
    else:
        tracks=' '.join('minmax(0,{:.4f}fr)'.format(max(1.0,float(widths[c['key']]))) for c in cols)
    css=CSS.replace('VAR_TRACKS',tracks)
    if data['projection']['layout']=='stacked':
        css += 'mcel-field{display:flex;gap:5px}mcel-field .field-label{min-width:70px;flex:none}'
    # The real MCEL registry chooses the DOM tags, not a separate pretend schema.
    registry=(SCRIPTS/'mcel-element-registry.js').read_text(encoding='utf-8')
    elements=(SCRIPTS/'mcel-elements-core.js').read_text(encoding='utf-8')
    runtime=(ROOT/'tools/mcel_information_quality_runtime.js').read_text(encoding='utf-8')
    options=''.join(f'<option value="{html.escape(c["key"],quote=True)}">{html.escape(c.get("label",c["key"]))}</option>' for c in cols)
    return ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>MCEL Information Quality · {html.escape(widget)} · {mode}</title><style>{css}</style>"
            f"<header><h1>{html.escape(widget)} — {mode}</h1><span id='mode'>Experimental MCEL registered collection</span></header>"
            f"<div class='toolbar'><input id='search' aria-label='Search original values' placeholder='Search original values'>"
            f"<select id='sort' aria-label='Sort using original typed values'>{options}</select></div>"
            "<main><section id='panel'><div id='mcel-collection-mount'></div></section>"
            "<aside id='mcel-inspector' aria-label='Exact selected values'></aside></main>"
            f"<script>{registry}</script><script>{elements}</script>"
            f"<script>window.MCEL_INFORMATION_CONTRACT={_safe_json(data)};</script>"
            f"<script>{runtime}</script></html>")


def baseline_and_after(fixture, widget, viewport):
    semantic=fixture['widgets'][widget]
    before=project_collection(semantic,fixture['value_sets'],viewport_px=viewport,enabled=False)
    after=project_collection(semantic,fixture['value_sets'],viewport_px=viewport,enabled=True)
    return before,after


def _browser_measure(page, data):
    page.wait_for_function('!!window.__MCEL_INFORMATION_FROB')
    return page.evaluate('''() => {
      const fields=[...document.querySelectorAll('mcel-field')];
      const sizeFields=fields.filter(f=>f.dataset.semanticField==='size');
      const visible=(el)=>{const r=el.getBoundingClientRect();return r.width>0&&r.left>=0&&r.right<=innerWidth+1};
      return {rows:document.querySelectorAll('mcel-row').length,
       collectionCount:document.querySelectorAll('mcel-collection').length,
       fieldCount:fields.length,
       width:innerWidth,
       visibleRequiredSize:sizeFields.length>0&&sizeFields.every(visible),
       actualHorizontalOverflow:document.documentElement.scrollWidth>innerWidth+1,
       compressed:fields.filter(f=>f.dataset.enabled==='true'&&f.querySelector('.field-value').textContent!==f.dataset.full).length,
       displayedCharacters:fields.reduce((n,f)=>n+f.querySelector('.field-value').textContent.length,0),
       identityFields:fields.map(f=>({row:f.dataset.sourceRowId,key:f.dataset.semanticField,raw:f.dataset.full,shown:f.querySelector('.field-value').textContent})),
       registeredTags:[...new Set(fields.map(f=>f.dataset.mcelElementId))],
      }
    }''')


def browser_proof(out, projections, *, chromium_executable=None):
    from playwright.sync_api import sync_playwright
    try:
        from tools.mcel_browser_launch import launch_chromium
    except ModuleNotFoundError:
        from mcel_browser_launch import launch_chromium
    report={}
    with sync_playwright() as p:
        browser,_launch_evidence=launch_chromium(p,executable=chromium_executable,headless=True)
        try:
            for widget,width,before,after in projections:
                for mode,doc in [('before',before),('after',after)]:
                    name=f'{widget}-{width}-{mode}'
                    page=browser.new_page(viewport={'width':width,'height':800},device_scale_factor=1)
                    errors=[]
                    page.on('pageerror',lambda err,errors=errors: errors.append(str(err)))
                    page.set_content((out/f'{name}.html').read_text(encoding='utf-8'),wait_until='load')
                    metrics=_browser_measure(page,doc)
                    rowid=doc['source_rows'][0]['id']
                    page.locator('mcel-row').first.focus()
                    page.keyboard.press('Enter')
                    exact=page.locator('#mcel-inspector').inner_text()
                    for col in doc['field_contracts']:
                        assert str(doc['source_rows'][0][col['key']]) in exact, (name,'inspect lost original',col['key'])
                    # Crucial: sorting uses exact bytes, not formatted KB strings.
                    numeric=[c for c in doc['field_contracts'] if c['key']=='size' and isinstance(doc['source_rows'][0]['size'],(int,float))]
                    if numeric:
                        page.locator('#sort').select_option('size')
                        sorted_ids=page.evaluate('() => window.__MCEL_INFORMATION_FROB.rows')
                        expected=[str(r['id']) for r in sorted(doc['source_rows'],key=lambda r:(r['size'],str(r['id'])))]
                        assert sorted_ids==expected,(name,'sort lost typed data',sorted_ids,expected)
                    # Find a substring absent from the compressed display.
                    raw=str(doc['source_rows'][0][doc['field_contracts'][0]['key']]);query=raw.split('_')[0]
                    page.locator('#search').fill(query)
                    found=page.evaluate('() => window.__MCEL_INFORMATION_FROB.rows')
                    assert str(rowid) in found,(name,'search lost raw value')
                    page.locator('#search').fill('')
                    page.screenshot(path=str(out/f'{name}.png'),full_page=True)
                    assert not errors,(name,errors)
                    assert metrics['collectionCount']==1 and metrics['rows']==len(doc['source_rows'])
                    assert metrics['fieldCount']==len(doc['source_rows'])*len(doc['field_contracts'])
                    expected_raw={(str(r['id']),c['key']): str(r.get(c['key'],'')) for r in doc['source_rows'] for c in doc['field_contracts']}
                    actual_raw={(r['row'],r['key']):r['raw'] for r in metrics['identityFields']}
                    assert expected_raw==actual_raw,(name,'DOM source binding mismatch')
                    assert not metrics['actualHorizontalOverflow'],(name,'horizontal overflow')
                    if widget=='downloads':assert metrics['visibleRequiredSize'],(name,'size starved')
                    report[name]={'status':'PASS',**{k:v for k,v in metrics.items() if k!='identityFields'},
                                  'raw_value_search':'PASS','typed_sort':'PASS' if numeric else 'N/A',
                                  'exact_inspection':'PASS','browser_errors':errors}
                    page.close()
        finally:browser.close()
    # Genuine negative controls: opt-out is not compressed and enabled projection
    # compresses some values in both unrelated semantic domains.
    for widget,width,before,after in projections:
        off=report[f'{widget}-{width}-before'];on=report[f'{widget}-{width}-after']
        assert off['compressed']==0,(widget,width,'negative control mutated')
        assert on['compressed']>0,(widget,width,'opt-in had no effect')
        assert on['displayedCharacters']<off['displayedCharacters'],(widget,width,'no compression benefit')
        assert not after['projection']['collisions'],(widget,width,'indistinguishable displayed rows')
        for col in after['field_contracts']:
            if col.get('presentation',{}).get('text_shrinker',{}).get('mode')!='contextual_identifier':
                continue
            key=col['key']
            shown=[r['cells'][key]['display'] for r in after['projection']['rows']]
            originals=[str(r[key]) for r in after['source_rows']]
            assert len(set(shown))==len(set(originals)),(widget,width,'identifier collision')
    return report


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--fixture',type=Path,default=DEFAULT_FIXTURE)
    ap.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    ap.add_argument('--no-browser',action='store_true')
    ap.add_argument('--chromium-executable')
    args=ap.parse_args(argv)
    fixture=load_flat_file(args.fixture)
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    projections=[]
    for widget in ('downloads','inventory'):
        for width in (320,390,1100):
            before,after=baseline_and_after(fixture,widget,width)
            projections.append((widget,width,before,after))
            for mode,doc in [('before',before),('after',after)]:
                name=f'{widget}-{width}-{mode}'
                (out/f'{name}.json').write_text(json.dumps(doc,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
                (out/f'{name}.html').write_text(page_for(doc,widget,mode),encoding='utf-8')
    browser=None if args.no_browser else browser_proof(out,projections,chromium_executable=args.chromium_executable)
    status='UNVERIFIED' if browser is None else 'PASS'
    summary={'schema':'mcel.information-quality-frob.v1','status':status,'production_integrated':False,
             'flat_file':str(args.fixture),'widgets':['downloads','inventory'],
             'viewports':[320,390,1100], 'browser':browser,
             'limitations':['Experimental MCEL registered collection, not a live application route',
                            'Human aesthetic judgment not certified','Pixel-width estimates remain heuristic']}
    (out/'report.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    entries=[]
    for widget,width,_,_ in projections:
        for mode in ('before','after'):
            name=f'{widget}-{width}-{mode}'
            if browser:
                entries.append(f"<section><h3>{name}</h3><img src='{name}.png' alt='{name}' loading='lazy'></section>")
            else:entries.append(f"<section><a href='{name}.html'>{name}</a></section>")
    gallery=("<!doctype html><meta charset='utf-8'><title>MCEL information quality Frob</title>"
             "<style>body{font:14px system-ui;margin:24px;background:#f4f7fb}section{background:white;"
             "padding:12px;margin:12px 0}img{max-width:100%;height:auto;border:1px solid #dde}</style>"
             "<h1>Information Quality: semantic opt-in before/after</h1>"+''.join(entries))
    (out/'gallery.html').write_text(gallery,encoding='utf-8')
    with ZipFile(out/'information-quality-review.zip','w',ZIP_DEFLATED) as z:
        for path in sorted(out.iterdir()):
            if path.is_file() and path.suffix in ('.png','.html','.json'):
                z.write(path,arcname=path.name)
    print('MCEL_INFORMATION_QUALITY_FROB_'+status)
    print('comparisons:',len(projections),'browser pages:',len(browser) if browser else 'unverified')
    print('report:',out/'report.json')
    print('gallery:',out/'gallery.html')
    print('review:',out/'information-quality-review.zip')
    return 0

if __name__=='__main__':
    sys.exit(main())
