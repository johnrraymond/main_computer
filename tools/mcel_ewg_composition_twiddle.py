"""Use the live E/W/G evidence collector to diagnose MCEL composition, read-only.

Runs the *same* browser diagnostic contract that the app copies on an E/W/G
click. No CSS injection, model requests, networking or layout edits.

    python -m tools.mcel_ewg_composition_twiddle
    python -m tools.mcel_ewg_composition_twiddle --widths 390 800 1272
"""
from __future__ import annotations
import argparse
import json
import shutil
from pathlib import Path
from main_computer.mcel_calculator_expanded_host import render_expanded_page

OUTPUT = Path('runtime/reports/mcel-ewg-twiddle')


def run(widths=(390, 800, 1272), *, output=OUTPUT, snapshots=True):
    from playwright.sync_api import sync_playwright
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    cases=[]
    with sync_playwright() as playwright:
        executable=shutil.which('chromium') or shutil.which('chromium-browser')
        browser=playwright.chromium.launch(headless=True,executable_path=executable or None)
        try:
            for width in widths:
                page=browser.new_page(viewport={'width':int(width),'height':900})
                try:
                    page.evaluate('''()=>{let data=new Map();const store={getItem:k=>data.get(k)||null,
                      setItem:(k,v)=>data.set(k,String(v)),removeItem:k=>data.delete(k),
                      clear:()=>data.clear(),key:i=>[...data.keys()][i]||null,get length(){return data.size}};
                      Object.defineProperty(window,'localStorage',{configurable:true,value:store});
                      Object.defineProperty(window,'sessionStorage',{configurable:true,value:store});}''')
                    page.route('**/*',lambda route:route.abort())
                    page.set_content(render_expanded_page(promoted=True))
                    page.locator('[data-mcel-mode-tabs] button[data-value="help"]').click()
                    for projection in ['conversation','notebook']:
                        if projection=='notebook':page.locator('[data-chat-conversation-switch]').click()
                        data=page.evaluate('''()=>{
                          const appId='semantic-calculator-expanded';
                          const witness=MCELDiagnosticsCounterWidget.refresh(appId);
                          const payload=MCELDiagnosticsCounterWidget._private.compactPayload(
                            witness.report,witness.counts,witness.history);
                          return {report:payload,visibleCounter:document.querySelector('[data-mcel-diagnostic-title-control]').textContent};
                        }''')
                        if snapshots:
                            name=f'ewg-{width}-{projection}.png'
                            page.locator('[data-mcel-region$=".help-workspace"]').screenshot(path=str(output/name))
                            data['screenshot']=name
                        cases.append({'width':int(width),'projection':projection,**data})
                finally:page.close()
        finally:browser.close()
    result={'schema':'mcel-ewg-twiddle-v1','method':'same EWG clipboard collector; no additional DOM diagnostic probe',
            'cases':cases}
    (output/'report.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--widths',type=int,nargs='+',default=[390,800,1272])
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--no-screenshots',action='store_true')
    args=parser.parse_args()
    report=run(args.widths,output=args.output,snapshots=not args.no_screenshots)
    for case in report['cases']:
        details=case['report']['diagnosticEvidence']
        print(case['width'],case['projection'],case['visibleCounter'],
              [item['code'] for item in details['findings']],details['coverage']['nestedCanonical']['excluded'])
    print('Report:',args.output/'report.json')


if __name__=='__main__':main()
