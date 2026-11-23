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


def test_tng_aperture_grille_is_quiet_on_buttons_in_all_states():
    """The E grille's actuator face must stay far quieter than CRT glass."""
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        try:
            page = _page(browser)
            data=page.evaluate(r'''() => {
              const root=document.getElementById('mcel-expanded-root');
              const region=root.querySelector('[data-mcel-region][data-mcel-purpose="input"]');
              region.insertAdjacentHTML('beforeend','<button id="grille-probe" type="button">Grille</button>');
              const button=document.getElementById('grille-probe');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const applied=McelSemanticChromePresentationLaw.apply({root,ir,
                bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
                theme:'theme-machine',profile:'chrome-tng-side-rails'});
              const alpha=role=>{
                const raw=root.style.getPropertyValue('--mcel-tng-aperture-'+role);
                const m=/data:image\/svg\+xml,([^"\)]+)/.exec(raw);
                const svg=new DOMParser().parseFromString(decodeURIComponent(m[1]),'image/svg+xml');
                const bright=[...svg.querySelectorAll('rect')].find(r=>r.getAttribute('x')==='1');
                return Number(bright.getAttribute('fill-opacity'));
              };
              return {applied,alpha:{button:alpha('button'),background:alpha('background'),display:alpha('display')},
                initialText:button.textContent};
            }''')
            assert data['applied']['status']=='applied',data
            assert data['alpha']['button'] < data['alpha']['background']/20,data
            assert data['alpha']['display'] > data['alpha']['background'],data
            button=page.locator('#grille-probe')
            def paint():
                return button.evaluate('''el=>({image:getComputedStyle(el).backgroundImage,
                    size:getComputedStyle(el).backgroundSize,shadow:getComputedStyle(el).textShadow,
                    text:el.textContent,wraps:el.querySelectorAll("[data-mcel-tng-gold-text]").length})''')
            idle=paint()
            button.hover()
            hovered=paint()
            page.mouse.down()
            pressed=paint()
            assert button.evaluate('el=>el.matches(":active")')
            page.mouse.up()
            for state,item in [('idle',idle),('hover',hovered),('pressed',pressed)]:
                assert '21px 6px' in item['size'],(state,item)
                assert item['image'].count('data:image/svg+xml,')==1,(state,item)
                assert item['shadow']=='none' and item['wraps']==0,(state,item)
                assert item['text']=='Grille', (state,item)
            assert idle['image']!=hovered['image']!=pressed['image']
        finally:
            browser.close()
