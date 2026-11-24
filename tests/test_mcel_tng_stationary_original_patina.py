"""The screen owns patina; native CSS samples it with control-state intensity."""
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from main_computer.mcel_worker_application import render_worker_page

ROOT=Path(__file__).resolve().parents[1]


def test_stationary_original_patina_on_worker_and_editor():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        try:
            for name in ('worker','code-editor'):
                page=browser.new_page(viewport={'width':1280,'height':575})
                html=render_worker_page(ROOT,canonical=True) if name=='worker' else render_semantic_page(name,ROOT,promoted=True)
                page.set_content(html,wait_until='domcontentloaded')
                page.wait_for_timeout(150)
                result=page.evaluate('''async () => {
                  const root=document.querySelector('[data-mcel-semantic-chrome="chrome-tng-side-rails"]');
                  const glass=document.querySelector('[data-mcel-tng-stationary-glass]');
                  const fields=[...root.querySelectorAll('input[data-mcel-chrome-material="recessed-field"]')];
                  const field=fields.find(e=>e.getBoundingClientRect().width>0&&!e.disabled)||[...root.querySelectorAll('button[data-mcel-chrome-material="raised-control"]')].find(e=>e.getBoundingClientRect().width>0&&!e.disabled&&!e.matches('[aria-selected="true"],[aria-pressed="true"]'));
                  const css=e=>getComputedStyle(e), rect=e=>e.getBoundingClientRect().toJSON();
                  if(!glass||!field)return {error:'missing glass or field'};
                  const before={image:css(field).backgroundImage,style:field.getAttribute('style'),rect:rect(field),glass:rect(glass)};
                  const rootImage=css(root).backgroundImage;
                  field.focus({preventScroll:true});
                  await new Promise(r=>setTimeout(r,130));
                  const after={image:css(field).backgroundImage,style:field.getAttribute('style'),rect:rect(field)};
                  const sc=[root,...root.querySelectorAll('*')].find(e=>e.scrollHeight>e.clientHeight+30&&e.clientHeight>100&&/auto|scroll/.test(css(e).overflowY));
                  if(sc)sc.scrollTop+=30;
                  await new Promise(r=>setTimeout(r,90));
                  const report={count:document.querySelectorAll('[data-mcel-tng-stationary-glass]').length,
                    oldOverlays:root.querySelectorAll('[data-mcel-tng-wear-surface]').length,
                    pointer:css(glass).pointerEvents, fixed:css(field).backgroundAttachment,
                    glassFixed:css(glass).backgroundAttachment, glassImage:css(glass).backgroundImage.startsWith('url('),
                    normalImage:before.image.startsWith('url('), highlighted:before.image!==after.image,
                    geometry:JSON.stringify(before.rect)===JSON.stringify(after.rect),
                    unchangedStyle:before.style===after.style,
                    stationary:JSON.stringify(before.glass)===JSON.stringify(rect(glass)),
                    scrolled:sc?.scrollTop||0,rootHasNoMovingWear:!rootImage.includes('data:image/webp'),
                    mainTransparent:css(root.querySelector(':scope > main')).backgroundColor==='rgba(0, 0, 0, 0)'};
                  McelSemanticChromePresentationLaw.clear(root);
                  report.cleared=!document.querySelector('[data-mcel-tng-stationary-glass]');
                  return report;
                }''')
                assert 'error' not in result,(name,result)
                assert result['count']==1 and result['oldOverlays']==0,(name,result)
                assert result['pointer']=='none' and result['fixed'].startswith('fixed') and result['glassFixed']=='fixed',(name,result)
                assert all(result[k] for k in ('glassImage','normalImage','highlighted','geometry','unchangedStyle','stationary','rootHasNoMovingWear','mainTransparent','cleared')),(name,result)
                if name=='code-editor': assert result['scrolled']>0,(name,result)
                page.close()
        finally: browser.close()
