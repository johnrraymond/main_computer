"""Complete visual-state Color Law: deterministic planning, not a new runtime consumer."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'
SCRIPTS = WEB / 'scripts'
STYLE = SCRIPTS / 'mcel-style-law.js'


def _page(browser):
    page = browser.new_page(viewport={'width': 900, 'height': 640})
    page.set_content('''<!doctype html><html><body>
      <div id="palette" class="mcel-runtime-preview theme-machine"><span id="sample">Contrast</span></div>
      <!-- The specimen is deliberately outside the legacy theme projector.
           This phase does not migrate application button selectors. -->
      <button id="lead" type="button">Previous</button>
      <button id="specimen" type="button" data-mcel-color-state="action-sample">Operation</button>
      </body></html>''')
    page.add_style_tag(path=str(WEB / 'styles/mcel-lab.css'))
    for name in ('mcel-contract.js', 'mcel-law-registry.js', 'mcel-style-law.js'):
        page.add_script_tag(path=str(SCRIPTS / name))
    return page


def test_v2_contract_is_additive_and_no_consumers_or_hover_event_listeners():
    style = STYLE.read_text(encoding='utf-8')
    assert 'function resolveColorTreatment(' in style
    assert 'function compileColorStateCss(' in style
    assert ':hover' in style and ':active' in style and ':focus-visible' in style
    assert 'addEventListener("mousemove"' not in style
    assert 'addEventListener("mouseenter"' not in style
    # E/W/G retains its v1 Color Law consumer; no new binding is activated.
    ewg = (SCRIPTS / 'mcel-diagnostics-counter-widget.js').read_text(encoding='utf-8')
    assert 'resolveColorTreatment' not in ewg
    projector = (SCRIPTS / 'mcel-semantic-theme-preview.js').read_text(encoding='utf-8')
    assert 'resolveColorTreatment' not in projector


def test_state_combinations_and_refusals_under_all_eight_theme_palettes():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _page(browser)
        report = page.evaluate('''() => {
          const law=McelLabStyleLaw, node=document.getElementById('palette');
          const cases=[
            {label:'rest'},
            {label:'hover',interaction:{hovered:true}},
            {label:'press',interaction:{hovered:true,pressed:true}},
            {label:'focused',interaction:{focused:true,focusVisible:true}},
            {label:'hover-focused',interaction:{hovered:true,focused:true,focusVisible:true}},
            {label:'pressed-focused',interaction:{hovered:true,pressed:true,focused:true,focusVisible:true}},
            {label:'selected',selection:'on'},
            {label:'selected-hover',selection:'on',interaction:{hovered:true}},
            {label:'mixed',selection:'mixed'},
            {label:'pending-focused',operation:'pending',interaction:{focused:true,focusVisible:true}},
            {label:'failed-pressed',operation:'failed',interaction:{hovered:true,pressed:true}},
            {label:'success-hover',operation:'succeeded',interaction:{hovered:true}},
            {label:'blocked-hover',availability:'disabled',interaction:{hovered:true}},
            {label:'unavailable',availability:'unavailable'},
            {label:'dangerous',consequence:'destructive'},
            {label:'critical',consequence:'critical'},
            {label:'forced-colors',environment:{forcedColors:true}},
            {label:'high-contrast',environment:{contrast:'more'}},
            {label:'keyboard-motion',interaction:{focused:true,focusVisible:true},environment:{reducedMotion:true}}
          ];
          const runs=[];
          for(const theme of law.themes){
            node.className='mcel-runtime-preview '+theme;
            const palette=law.semanticPaletteFor(node,getComputedStyle);
            const base={theme,role:'action.generic',surface:'control',context:'dialog',palette};
            const decisions=cases.map(c=>{
              const {label,...dimensions}=c;
              const decision=law.resolveColorTreatment({...base,...dimensions});
              return {name:label,decision,repeat:law.resolveColorTreatment({...base,...dimensions})};
            });
            const d=Object.fromEntries(decisions.map(r=>[r.name,r.decision]));
            const compiled=law.compileColorStateCss({id:'action-sample',...base});
            runs.push({theme,
              statuses:decisions.map(r=>r.decision.status),
              pure:decisions.every(r=>JSON.stringify(r.decision)===JSON.stringify(r.repeat)),
              frozen:decisions.every(r=>Object.isFrozen(r.decision)&&Object.isFrozen(r.decision.channels)),
              ids:decisions.map(r=>r.name),
              backgrounds:[d.rest.channels.background,d.hover.channels.background,d.press.channels.background],
              focusRing:[d.rest.channels.focusRing,d.focused.channels.focusRing,d['pressed-focused'].channels.focusRing],
              selected:d['selected-hover'].channels.selection,
              selectedBackgrounds:[d.selected.channels.background,d['selected-hover'].channels.background],
              pressedBackgrounds:[d.press.channels.background,d['pressed-focused'].channels.background],
              pending:d['pending-focused'].channels.status,
              failed:d['failed-pressed'].channels.status,
              success:d['success-hover'].channels.status,
              blockedHover:d['blocked-hover'].channels,
              blockedRest:law.resolveColorTreatment({...base,availability:'disabled'}).channels,
              highContrast:d['high-contrast'].channels,
              forced:d['forced-colors'].channels,
              danger:d.dangerous.channels.accent,
              routine:d.rest.channels.accent,
              css:compiled.status==='resolved' && compiled.css.includes(':focus-visible') &&
                compiled.css.includes('@media (forced-colors: active)') &&
                compiled.css.includes('@media (prefers-contrast: more)') && compiled.paintWrites===0,
              phases:compiled.phases?.length
            });
          }
          const input={theme:'theme-machine',role:'action.start',surface:'control'};
          node.className='mcel-runtime-preview theme-machine';
          const colors=law.semanticPaletteFor(node,getComputedStyle);
          const refuse=[
            law.resolveColorTreatment({...input,interaction:{focusVisible:true}}).reason,
            law.resolveColorTreatment({...input,interaction:{pressed:true},availability:'disabled'}).reason,
            law.resolveColorTreatment({...input,interaction:{focusVisible:'yes'}}).reason,
            law.resolveColorTreatment({...input,interaction:{mouseover:true}}).reason,
            law.resolveColorTreatment({...input,environment:{contrast:'extra'}}).reason,
            law.resolveColorTreatment({...input,environment:{reducedMotion:0}}).reason,
            law.resolveColorTreatment({...input,consequence:'fatal'}).reason,
            law.resolveColorTreatment({...input,availability:'blocking'}).reason,
            law.resolveColorTreatment({...input,unknownDimension:'oops'}).reason,
            law.resolveColorTreatment({...input,selection:'on',surface:'text'}).reason,
            law.resolveColorTreatment({...input,role:'diagnostic.error',consequence:'critical'}).reason,
            law.resolveColorTreatment({...input,theme:'oops'}).reason,
            law.compileColorStateCss({id:'a;body{display:none}',...input}).reason,
            law.compileColorStateCss({id:'a',...input,palette:colors,interaction:{hovered:true}}).reason,
            law.compileColorStateCss({id:'a',...input,palette:colors,availability:'disabled'}).status,
            law.compileColorStateCss({id:'a',...input}).reason,
            law.compileColorStateCss({id:'a',...input,palette:{...colors,'--mcel-theme-error':'url(x)'}}).reason
          ];
          return {runs,refuse,legacy:law.resolveColor({theme:'theme-machine',role:'diagnostic.error',surface:'text'}),
            outputKeys:Object.keys(law.resolveColorTreatment(input)),stateVocabulary:law.semanticVisualStates};
        }''')
        browser.close()
    assert len(report['runs']) == 8
    assert report['refuse'] == [
        'focus-visible-without-focus','pressed-unavailable-control',
        'invalid-interaction-focusVisible','unknown-interaction-dimension',
        'unknown-contrast','invalid-environment-reducedMotion',
        'unknown-consequence','unknown-availability','unknown-input-dimension',
        'selection-on-nonselectable-surface','consequence-on-non-action','unknown-theme',
        'invalid-style-identity','interaction-is-css-owned','resolved',
        'computed-theme-palette-required','unverifiable-theme-palette'
    ], report['refuse']
    assert report['legacy']['schema'] == 'mcel.semantic-color.v1'
    assert report['stateVocabulary']['operation'] == ['idle','pending','succeeded','failed']
    for r in report['runs']:
        assert set(r['statuses']) == {'resolved'} and r['pure'] and r['frozen'], r
        assert len(set(r['backgrounds'])) == 3, r
        assert r['focusRing'][0] == 'transparent' and r['focusRing'][1] == r['focusRing'][2] != 'transparent', r
        assert r['selected'] != 'transparent' and r['pending'] != r['failed'] != r['success'], r
        assert r['selectedBackgrounds'][0] != r['selectedBackgrounds'][1], r
        assert r['blockedHover'] == r['blockedRest'], r
        assert r['forced']['foreground'] == 'CanvasText', r
        assert r['danger'] != r['routine'] and r['css'] and r['phases'] == 8, r


def test_compiled_css_uses_native_hover_active_focus_and_no_paint_mutation():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _page(browser)
        css = page.evaluate('''() => {
          const law=McelLabStyleLaw,palette=law.semanticPaletteFor(document.getElementById('palette'),getComputedStyle);
          window.__lawInput={theme:'theme-machine',role:'action.generic',surface:'control',palette};
          const output=law.compileColorStateCss({id:'action-sample',...window.__lawInput});
          return output.css;
        }''')
        page.add_style_tag(content=css)
        def snapshot():
            return page.locator('#specimen').evaluate('''el=>({
              foreground:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor,
              border:getComputedStyle(el).borderTopColor,outline:getComputedStyle(el).outlineColor,
              accent:getComputedStyle(el).getPropertyValue('--mcel-color-accent').trim(),
              status:getComputedStyle(el).getPropertyValue('--mcel-color-status').trim(),
              size:[el.getBoundingClientRect().width,el.getBoundingClientRect().height],
              identity:el===document.getElementById('specimen')})''')
        resting=snapshot()
        page.locator('#specimen').hover()
        hovered=snapshot()
        page.mouse.down()
        pressed=snapshot()
        page.mouse.up()
        released=snapshot()
        # Keyboard focus-visible must retain a distinct color channel independently
        # of the current pointer treatment and without changing the control DOM.
        page.mouse.move(0, 0)
        page.locator('#lead').focus()
        page.keyboard.press('Tab')
        focus=snapshot()
        assert page.locator('#specimen').evaluate('el=>el.matches(":focus-visible")')
        page.keyboard.down('Space')
        keyboard_pressed=snapshot()
        page.keyboard.up('Space')
        keyboard_released=snapshot()
        expected = page.evaluate('''() => {
          const law=McelLabStyleLaw,input=window.__lawInput;
          const d=(interaction)=>law.resolveColorTreatment({...input,interaction}).channels;
          const contexts=[{}, {hovered:true},{hovered:true,pressed:true},{focused:true,focusVisible:true}];
          const canvas=document.createElement('span');document.body.appendChild(canvas);
          const resolved=contexts.map(state=>{
            const ch=d(state);canvas.style.color=ch.foreground;canvas.style.backgroundColor=ch.background;
            canvas.style.borderColor=ch.border;canvas.style.outlineColor=ch.focusRing;
            const cs=getComputedStyle(canvas);
            return {foreground:cs.color,background:cs.backgroundColor,
              border:cs.borderTopColor,outline:cs.outlineColor};
          });canvas.remove();return resolved;
        }''')
        before=page.evaluate('''() => ({styles:document.querySelectorAll('[data-mcel-color-law-applied]').length,
           semantic:document.getElementById('specimen').getAttribute('data-mcel-color-role')})''')
        browser.close()
    assert resting['identity'] and hovered['identity'] and pressed['identity'] and released['identity'] and focus['identity']
    for actual, resolved in zip((resting,hovered,pressed,focus),expected):
        for key in ('foreground','background','border','outline'):
            assert actual[key] == resolved[key], (key,actual,resolved)
    assert resting['background'] != hovered['background'] != pressed['background']
    assert released['background'] == hovered['background']
    assert keyboard_pressed['background'] == pressed['background']
    assert keyboard_pressed['outline'] == focus['outline']
    assert keyboard_released['background'] == focus['background']
    assert pressed['size'] == resting['size'] == focus['size']
    assert resting['outline'] != focus['outline']
    assert before == {'styles':0,'semantic':None}


def test_lab_read_only_inspector_includes_composed_state_examples_but_does_not_paint():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    html = (WEB / 'apps/mcel-lab.html').read_text(encoding='utf-8')
    script = (SCRIPTS / 'mcel-lab.js').read_text(encoding='utf-8')
    start=script.index('    // Observational semantic Color Law inspection.')
    end=script.index('    function initMcelLabApp()',start)
    part=script[start:end]
    fixture='''<!doctype html><html><body><div id="mcel-runtime-preview" class="mcel-runtime-preview theme-machine">
      <button id="reference">Stop</button></div>'''
    fixture+=html[html.index('    <details id="mcel-semantic-color-inspector"'):
                  html.index('  </section>\n  <section class="mcel-site-skeleton"')]
    fixture+='</body></html>'
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=browser.new_page()
        page.set_content(fixture)
        page.add_style_tag(path=str(WEB/'styles/mcel-lab.css'))
        for name in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js'):
            page.add_script_tag(path=str(SCRIPTS/name))
        page.add_script_tag(content='''var mcelLabState={theme:'theme-machine'};
          var mcelThemeSelect={value:'theme-machine'};
          var mcelRuntimePreview=document.getElementById('mcel-runtime-preview');\n'''+part)
        report=page.evaluate('''() => {
          const btn=document.getElementById('reference');const detail=document.getElementById('mcel-semantic-color-inspector');
          const prior=[getComputedStyle(btn).color,getComputedStyle(btn).backgroundColor];
          detail.open=true;
          const r=renderMcelSemanticColorInspector();
          const nested=document.querySelector('.mcel-semantic-color-state-report');
          const text=nested?.querySelector('pre')?.textContent||'';
          const next=[getComputedStyle(btn).color,getComputedStyle(btn).backgroundColor];
          return {r,prior,next,lines:text.split('\\n').length,hasPressed:text.includes('pressed:'),
            hasPending:text.includes('pending+focus'),hasDisabled:text.includes('disabled+hover'),
            styleCount:document.querySelectorAll('[data-mcel-color-law-applied]').length};
        }''')
        browser.close()
    assert report['r']['status'] == 'observed'
    assert report['r']['visualStateCases'] == 8
    assert report['lines'] == 8 and report['hasPressed'] and report['hasPending'] and report['hasDisabled']
    assert report['prior'] == report['next'] and report['styleCount'] == 0


def test_wcag_normal_text_contrast_for_composed_states_across_eight_themes():
    """Contrast must hold AFTER hover, press, selection and status composition."""
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _page(browser)
        report = page.evaluate('''() => {
          const law=McelLabStyleLaw,el=document.getElementById('palette'),sample=document.getElementById('sample');
          const canvas=document.createElement('canvas');canvas.width=canvas.height=1;const ctx=canvas.getContext('2d');
          const paint=(color,back)=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=back;ctx.fillRect(0,0,1,1);
            ctx.fillStyle=color;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].slice(0,3)};
          const luminance=rgb=>rgb.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;})
            .reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
          const contrast=(a,b)=>{const x=luminance(a),y=luminance(b);
            return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)};
          const cases=[['rest',{}],['hover',{interaction:{hovered:true}}],
            ['press',{interaction:{pressed:true}}],['selected',{selection:'on'}],
            ['selectedPressed',{selection:'on',interaction:{pressed:true}}],
            ['pending',{operation:'pending'}],['failed',{operation:'failed'}],
            ['destructive',{consequence:'destructive'}],
            ['criticalPressed',{consequence:'critical',interaction:{pressed:true}}]];
          const roles=['action.generic','action.start','action.stop',
            'diagnostic.error','diagnostic.warning','diagnostic.success'];
          const findings=[];
          for(const theme of law.themes){el.className='mcel-runtime-preview '+theme;
            const palette=law.semanticPaletteFor(el,getComputedStyle);
            for(const role of roles){for(const [caseName,dimensions] of cases){
              if(role.startsWith('diagnostic.')&&dimensions.consequence)continue;
              const d=law.resolveColorTreatment({theme,role,
                surface:role.startsWith('diagnostic.')?'indicator':'control',palette,...dimensions});
              if(d.status!=='resolved'){findings.push({theme,role,caseName,reason:d.reason});continue;}
              sample.style.color=d.channels.foreground;sample.style.backgroundColor=d.channels.background;
              const css=getComputedStyle(sample);
              const ratio=Math.min(...['black','white'].map(back=>
                contrast(paint(css.color,back),paint(css.backgroundColor,back))));
              findings.push({theme,role,caseName,ratio});
            }}
          }
          return {total:findings.length,worst:findings.sort((a,b)=>a.ratio-b.ratio).slice(0,8),
            failed:findings.filter(x=>!(x.ratio>=4.5)).slice(0,10)};
        }''')
        browser.close()
    assert report['total'] == (8 * (3*9 + 3*7)), report
    assert not report['failed'], report


def test_coarse_touch_hover_suppression_and_forced_colors_fallback():
    """CSS can handle mouse, keyboard, touch and OS forced colors without JS repaint."""
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = browser.new_page(viewport={'width':390,'height':740},has_touch=True,is_mobile=True)
        page.set_content('''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>
          <div id="palette" class="mcel-runtime-preview theme-machine"></div>
          <button id="specimen" data-mcel-color-state="touch-sample">Touch</button>
          </body></html>''')
        page.add_style_tag(path=str(WEB/'styles/mcel-lab.css'))
        for name in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js'):
            page.add_script_tag(path=str(SCRIPTS/name))
        css = page.evaluate('''() => {
          const law=McelLabStyleLaw,palette=law.semanticPaletteFor(document.getElementById('palette'),getComputedStyle);
          return law.compileColorStateCss({id:'touch-sample',theme:'theme-machine',role:'action.generic',surface:'control',palette}).css;
        }''')
        page.add_style_tag(content=css)
        match=page.evaluate('''() => ({fine:matchMedia('(hover: hover) and (pointer: fine)').matches,
          touch:matchMedia('(hover: none)').matches})''')
        def sample():
            return page.locator('#specimen').evaluate('''el=>({background:getComputedStyle(el).backgroundColor,
                foreground:getComputedStyle(el).color,border:getComputedStyle(el).borderTopColor,
                hover:el.matches(':hover'),active:el.matches(':active'),focus:el.matches(':focus-visible')})''')
        rest=sample()
        page.locator('#specimen').tap()
        page.wait_for_timeout(350)
        after=sample()
        page.emulate_media(forced_colors='active')
        forced=sample()
        page.emulate_media(forced_colors='none')
        restored=sample()
        browser.close()
    assert match == {'fine':False,'touch':True}, match
    colors=lambda record:{key:record[key] for key in ('background','foreground','border')}
    assert not rest['active'] and not after['active'] and not restored['active']
    assert colors(rest) == colors(after) == colors(restored), (rest,after,restored)
    # Chromium may retain :hover after a tap, but coarse-pointer CSS must
    # not paint a sticky hover treatment or a lingering press treatment.
    assert forced['background'] != rest['background'], (forced,rest)


def test_semantic_state_recompile_keeps_selection_operation_and_focus_separate():
    """Only persistent state changes regenerate CSS; browser interaction does not."""
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = _page(browser)
        result=page.evaluate('''() => {
          const law=McelLabStyleLaw,palette=law.semanticPaletteFor(document.getElementById('palette'),getComputedStyle);
          const source={theme:'theme-machine',role:'action.inspect',surface:'control',palette};
          const element=document.getElementById('specimen');
          const sheet=document.createElement('style');document.head.appendChild(sheet);
          const observations=[];
          for(const [label,dimensions] of [
            ['selected+pending',{selection:'on',operation:'pending'}],
            ['selected+failed',{selection:'on',operation:'failed'}],
            ['disabled+pending',{selection:'on',operation:'pending',availability:'disabled'}],
            ['selected+succeeded',{selection:'on',operation:'succeeded'}]]){
            const result=law.compileColorStateCss({id:'action-sample',...source,...dimensions});
            const repeated=law.compileColorStateCss({id:'action-sample',...source,...dimensions});
            const resolution=law.resolveColorTreatment({...source,...dimensions});
            sheet.textContent=result.css;
            const c=getComputedStyle(element);
            observations.push({label,deterministic:JSON.stringify(result)===JSON.stringify(repeated),
              status:result.status,border:c.borderTopColor,
              selection:c.getPropertyValue('--mcel-color-selection').trim(),
              signal:c.getPropertyValue('--mcel-color-status').trim(),
              accent:c.getPropertyValue('--mcel-color-accent').trim(),
              expectedSelection:resolution.channels.selection,expectedStatus:resolution.channels.status,
              bounding:[element.offsetWidth,element.offsetHeight]});
          }
          sheet.remove();return observations;
        }''')
        browser.close()
    assert len(result) == 4
    assert all(x['status'] == 'resolved' and x['deterministic'] for x in result), result
    assert len({tuple(x['bounding']) for x in result}) == 1, result
    assert len({x['signal'] for x in result}) == 3, result
    assert len({x['selection'] for x in result}) == 1, result
    assert len({x['border'] for x in result}) > 1, result
    assert all(x['selection'] == x['expectedSelection'] and x['signal'] == x['expectedStatus']
               for x in result), result
