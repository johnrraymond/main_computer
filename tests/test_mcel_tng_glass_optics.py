"""Regression for the native Chrome-owned horizontal LCD and pointer-local escaping glitter."""
from pathlib import Path
import shutil
import pytest
from playwright.sync_api import sync_playwright
from main_computer.mcel_worker_application import render_worker_page

ROOT = Path(__file__).resolve().parents[1]
CHROME = '[data-mcel-semantic-chrome="chrome-tng-side-rails"]'
GLASS = '[data-mcel-tng-optical-glass]'
LCD = '[data-mcel-tng-optical-lcd]'
LCD_HOST = '[data-mcel-tng-optical-lcd-host]'
GLITTER = '[data-mcel-tng-localized-glitter]'


@pytest.mark.parametrize('dpr', [1, 2])
def test_worker_horizontal_lcd_and_localized_glitter(dpr):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            executable_path=shutil.which('chromium'),
            args=['--no-sandbox'],
        )
        try:
            page = browser.new_page(
                viewport={'width': 1280, 'height': 575},
                device_scale_factor=dpr,
            )
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.set_content(
                render_worker_page(ROOT, canonical=True),
                wait_until='domcontentloaded',
            )
            page.wait_for_selector(GLASS)
            chrome = page.locator(CHROME)
            glass = page.locator(GLASS)
            assert page.locator('[data-mcel-tng-optical-ambient]').count() == 0
            assert page.locator('[data-mcel-tng-optical-spark]').count() == 0
            assert page.locator('[data-mcel-tng-optical-ray]').count() == 0
            assert page.locator('[data-mcel-tng-suspended-glints]').count() == 0
            assert glass.evaluate('e=>e.inert && getComputedStyle(e).pointerEvents==="none"')

            glitter = page.locator(GLITTER)
            assert glitter.count() == 1
            assert not glitter.is_visible()
            assert glitter.evaluate('e=>e.inert && getComputedStyle(e).pointerEvents==="none"')
            button = chrome.locator('button:visible:enabled').first
            button.hover()
            page.wait_for_selector(LCD)
            lines = glass.locator(LCD)
            assert lines.count() == 8
            assert lines.first.evaluate(
                'e=>[e.width,e.height,getComputedStyle(e).imageRendering]'
            ) == [1, 575 * dpr, 'pixelated']
            assert page.locator('[data-mcel-tng-optical-ambient]').count() == 0
            assert page.locator('[data-mcel-tng-optical-spark]').count() == 0
            assert page.locator('[data-mcel-tng-optical-ray]').count() == 0

            page.evaluate(
                'window.testOpticalRaster=document.querySelector("[data-mcel-tng-optical-lcd]")'
            )
            bounds = button.bounding_box()
            page.mouse.move(bounds['x'] + bounds['width'] * 0.6, bounds['y'] + bounds['height'] * 0.5)
            page.wait_for_timeout(100)
            assert page.evaluate(
                'testOpticalRaster===document.querySelector("[data-mcel-tng-optical-lcd]")'
            )

            # Strike the bottom edge so the localized field crosses it.
            button.hover(position={'x': bounds['width'] * .5, 'y': bounds['height'] - 2})
            page.wait_for_selector(GLITTER)
            assert glitter.evaluate('''e=>{
              const r=e.getBoundingClientRect(), d=devicePixelRatio;
              const g=document.querySelector('[data-mcel-tng-pointer-glow]').getBoundingClientRect();
              return r.width<=50+2/d && r.height<=50+2/d &&
                e.width===Math.round(r.width*d) && e.height===Math.round(r.height*d) && r.bottom>g.bottom;
            }''')
            # Inspect actual raster output, not merely the canvas bounds.
            page.wait_for_function('''()=>{
              const e=document.querySelector('[data-mcel-tng-localized-glitter]');
              const r=e.getBoundingClientRect(),d=devicePixelRatio;
              const g=document.querySelector('[data-mcel-tng-pointer-glow]').getBoundingClientRect();
              const a=e.getContext('2d').getImageData(0,0,e.width,e.height).data;
              let square=false,escaped=false;
              for(let y=0;y<e.height-1;y++)for(let x=0;x<e.width-1;x++){
                const k=(y*e.width+x)*4+3,alpha=a[k];
                if(!alpha)continue;
                if(r.top+(y+.5)/d>g.bottom)escaped=true;
                if(a[k+4]===alpha && a[k+4*e.width]===alpha && a[k+4*e.width+4]===alpha)square=true;
              }
              return square&&escaped;
            }''', timeout=12000)
            button.evaluate('e=>e.disabled=true')
            page.wait_for_function(
                'document.querySelector("[data-mcel-tng-optical-lcd-host]").style.display==="none"'
            )
            assert page.locator('[data-mcel-tng-optical-ambient]').count() == 0
            assert page.locator('[data-mcel-tng-optical-spark]').count() == 0
            assert page.locator('[data-mcel-tng-optical-ray]').count() == 0

            assert not glitter.is_visible()
            button.evaluate('e=>e.disabled=false')
            page.mouse.move(1, 1)
            page.wait_for_timeout(150)
            assert page.locator('[data-mcel-tng-optical-ambient]').count() == 0
            assert page.locator('[data-mcel-tng-optical-spark]').count() == 0
            assert page.locator('[data-mcel-tng-optical-ray]').count() == 0

            page.evaluate(f'McelSemanticChromePresentationLaw.clear(document.querySelector({CHROME!r}))')
            assert glass.count() == 0
            assert glitter.count() == 0
            assert page.locator('[data-mcel-tng-optical-style]').count() == 0
            assert errors == [], errors
        finally:
            browser.close()
