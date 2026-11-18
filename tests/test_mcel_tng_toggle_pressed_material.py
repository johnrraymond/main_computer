"""Latched semantic buttons use the held-down material without hover winning."""

from playwright.sync_api import sync_playwright

from tests.test_mcel_semantic_chrome_universal_material import _page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome


def test_generic_aria_pressed_toggle_looks_depressed_and_hover_does_not_override():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        try:
            page = _page(browser)
            result = page.evaluate('''() => {
              const root=document.getElementById('mcel-expanded-root');
              const host=root.querySelector('[data-mcel-region][data-mcel-purpose="input"]');
              const controls=document.createElement('div');
              controls.innerHTML=`<button id="latch" type="button" aria-pressed="true">Info</button>
                <button id="momentary" type="button">Action</button>
                <button id="ordinary" type="button">Ordinary</button>`;
              host.appendChild(controls);
              document.getElementById('latch').addEventListener('click',e=>{
                const btn=e.currentTarget;
                btn.setAttribute('aria-pressed',String(btn.getAttribute('aria-pressed')!=='true'));
              });
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const applied=McelSemanticChromePresentationLaw.apply({root,ir,
                bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
                theme:'theme-machine',profile:'chrome-tng-side-rails'});
              return {status:applied.status,material:document.getElementById('latch').getAttribute('data-mcel-chrome-material')};
            }''')
            assert result == {'status': 'applied', 'material': 'raised-control'}, result
            def paint(btn):
                return btn.evaluate('''el=>{
                  const cs=getComputedStyle(el);
                  const wear=el.querySelector(':scope > [data-mcel-tng-wear-surface="button"]');
                  const glint=wear?.querySelector('[data-mcel-tng-wear-glint]');
                  return {image:cs.backgroundImage,shadow:cs.boxShadow,
                    wearMask:wear?getComputedStyle(wear).maskImage:null,
                    wearOpacity:wear?getComputedStyle(wear).opacity:null,
                    glintMask:glint?getComputedStyle(glint).maskImage:null,
                    glintOpacity:glint?getComputedStyle(glint).opacity:null};
                }''')
            latch=page.locator('#latch')
            momentary=page.locator('#momentary')
            ordinary=page.locator('#ordinary')
            page.mouse.move(0,0)
            page.wait_for_timeout(180)
            selected_not_hovered=paint(latch)
            latch.hover()
            page.wait_for_timeout(180)
            assert paint(latch)==selected_not_hovered, 'hover over selected toggle must not change material'
            page.mouse.move(0,0)
            page.wait_for_timeout(180)
            ordinary_raised=paint(momentary)
            momentary.hover()
            page.wait_for_timeout(180)
            ordinary_hovered=paint(momentary)
            page.mouse.down()
            # Native :active must change the actual face at mouse-down, not
            # merely the decorative wear child after a transition delay.
            held=paint(momentary)
            assert momentary.evaluate("el => el.matches(':active')")
            assert selected_not_hovered==held,(selected_not_hovered,held)
            assert held['shadow'] != ordinary_raised['shadow'], (held,ordinary_raised)
            assert held['shadow'] != ordinary_hovered['shadow'], (held,ordinary_hovered)
            assert held['image'] != ordinary_hovered['image'], (held,ordinary_hovered)
            assert '3px 3px 6px' in held['shadow'], held
            assert '2px 3px 3px' not in held['shadow'], held
            page.mouse.up()
            page.mouse.move(0,0)
            page.wait_for_timeout(180)
            assert paint(momentary) == ordinary_raised, 'released momentary button must return to its raised state'
            # A toggle should revert to the same raised material even if the
            # pointer remains over it at the instant of the state transition.
            latch.click()
            page.wait_for_timeout(180)
            assert latch.get_attribute('aria-pressed')=='false'
            off_hovered=paint(latch)
            page.mouse.move(0,0)
            page.wait_for_timeout(180)
            assert paint(latch)==off_hovered, 'off toggle must not retain a hover highlight'
            # Ordinary momentary buttons still provide a hover affordance.
            idle=paint(ordinary)
            ordinary.hover()
            page.wait_for_timeout(180)
            assert paint(ordinary)!=idle, 'hover should still work for ordinary controls'
        finally:
            browser.close()


def test_workspace_stipple_is_shared_by_default_tng_buttons_in_all_states():
    """One pixelated, Theme-colored tile is shared by background and controls."""
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        try:
            page = _page(browser)
            baseline = page.evaluate('''() => {
              const root=document.getElementById('mcel-expanded-root');
              const host=root.querySelector('[data-mcel-region][data-mcel-purpose="input"]');
              host.insertAdjacentHTML('beforeend',
                '<button id="stipple-probe" type="button">Stipple</button>');
              const button=document.getElementById('stipple-probe');
              const before=getComputedStyle(button);
              const color={background:before.backgroundColor,foreground:before.color,
                border:before.borderColor};
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const result=McelSemanticChromePresentationLaw.apply({root,ir,
                bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
                theme:'theme-machine',profile:'chrome-tng-side-rails'});
              return {status:result.status,color};
            }''')
            assert baseline['status'] == 'applied', baseline

            button = page.locator('#stipple-probe')
            def paint():
                return page.evaluate('''() => {
                  const root=document.getElementById('mcel-expanded-root');
                  const button=document.getElementById('stipple-probe');
                  const rootStyle=getComputedStyle(root),style=getComputedStyle(button);
                  return {workspaceImage:rootStyle.backgroundImage,
                    workspaceSize:rootStyle.backgroundSize,
                    image:style.backgroundImage,size:style.backgroundSize,
                    color:{background:style.backgroundColor,foreground:style.color,
                      border:style.borderColor}};
                }''')

            page.mouse.move(0, 0)
            idle = paint()
            assert 'url("data:image/svg+xml,' in idle['workspaceImage'], idle
            assert idle['workspaceImage'].count('data:image/svg+xml,') == 1, idle
            assert '8px 8px' in idle['workspaceSize'], idle
            assert idle['workspaceImage'].count('url(') == 1, idle
            texture = page.evaluate(r'''() => {
              const value=document.getElementById('mcel-expanded-root')
                .style.getPropertyValue('--mcel-tng-stipple');
              const uri=/data:image\/svg\+xml,([^"']+)/.exec(value);
              if (!uri) return {error:value};
              const svg=new DOMParser().parseFromString(
                decodeURIComponent(uri[1]), 'image/svg+xml');
              return {width:svg.documentElement.getAttribute('width'),
                height:svg.documentElement.getAttribute('height'),
                pixels:[...svg.querySelectorAll('rect')].map(node=>({
                  x:+node.getAttribute('x'),y:+node.getAttribute('y'),
                  alpha:+node.getAttribute('fill-opacity')}))};
            }''')
            assert texture['width'] == texture['height'] == '8', texture
            assert len(texture['pixels']) > 35, texture
            hot = [pixel for pixel in texture['pixels'] if pixel['alpha'] == .35]
            dim = [pixel for pixel in texture['pixels'] if pixel['alpha'] < .35]
            assert hot == [{'x':0,'y':0,'alpha':.35}], texture
            assert len(dim) > 34 and len(set(pixel['alpha'] for pixel in dim)) >= 4, texture
            # Decode the actual SVG tile in Chromium: a CSS URL alone does not
            # prove that the intermediate pixels paint on the display.
            raster = page.evaluate(r'''async () => {
              const raw=document.getElementById('mcel-expanded-root')
                .style.getPropertyValue('--mcel-tng-stipple');
              const src=/url\("([^"]+)"\)/.exec(raw)?.[1];
              const image=new Image(); image.src=src;
              await image.decode();
              const canvas=document.createElement('canvas');
              canvas.width=8; canvas.height=8;
              const ctx=canvas.getContext('2d');
              ctx.drawImage(image, 0, 0);
              const pixels=ctx.getImageData(0,0,8,8).data;
              return [...Array(64)].map((_,i)=>pixels[i*4+3]);
            }''')
            assert len(raster) == 64 and raster[0] > 75, raster
            assert 35 < sum(0 < pixel < raster[0] for pixel in raster) < 64, raster
            assert len(set(pixel for pixel in raster if 0 < pixel < raster[0])) >= 4, raster
            hovered = None
            for state in ('idle', 'hover', 'pressed'):
                if state == 'hover':
                    button.hover()
                elif state == 'pressed':
                    page.mouse.down()
                observed = paint()
                if state == 'hover':
                    hovered = button.evaluate('el => getComputedStyle(el).boxShadow')
                if state == 'pressed':
                    assert button.evaluate("el => el.matches(':active')")
                    assert button.evaluate('el => getComputedStyle(el).boxShadow') != hovered
                    assert '3px 3px 6px' in button.evaluate('el => getComputedStyle(el).boxShadow')
                assert 'linear-gradient(' in observed['image'], (state, observed)
                assert 'url("data:image/svg+xml,' in observed['image'], (state, observed)
                assert observed['image'].count('url(') == 1, (state, observed)
                assert '8px 8px' in observed['size'], (state, observed)
                assert observed['color'] == baseline['color'], (state, observed)
            page.mouse.up()
            removed = page.evaluate('''() => {
              McelSemanticChromePresentationLaw.clear(document.getElementById('mcel-expanded-root'));
              return getComputedStyle(document.getElementById('stipple-probe')).backgroundImage;
            }''')
            assert removed == 'none', removed
        finally:
            browser.close()
