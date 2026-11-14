"""Patch 1: canonical semantic Color Law contract, no application palette authority."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'


def test_color_contract_v3_is_deterministic_in_every_existing_theme():
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = browser.new_page()
        page.set_content('<div id="swatch" class="mcel-runtime-preview theme-machine"></div>')
        page.add_style_tag(path=str(WEB/'styles/mcel-lab.css'))
        for name in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js'):
            page.add_script_tag(path=str(WEB/'scripts'/name))
        result = page.evaluate('''() => {
          const law=McelLabStyleLaw,swatch=document.getElementById('swatch');
          const declaration={role:'action.inspect',surface:'control',context:'toolbar',consequence:'routine'};
          const out=[];
          for(const theme of law.themes){
            swatch.className='mcel-runtime-preview '+theme;
            const palette=law.semanticPaletteFor(swatch,getComputedStyle);
            const rest=law.resolveDeclaredColor({theme,declaration,kind:'control',palette});
            const hover=law.resolveDeclaredColor({theme,declaration,kind:'control',palette,interaction:{hovered:true}});
            const pressed=law.resolveDeclaredColor({theme,declaration,kind:'control',palette,interaction:{pressed:true}});
            const focused=law.resolveDeclaredColor({theme,declaration,kind:'control',palette,interaction:{focused:true,focusVisible:true}});
            const disabled=law.resolveDeclaredColor({theme,declaration,kind:'control',palette,availability:'disabled'});
            const selected=law.resolveDeclaredColor({theme,declaration,kind:'control',palette,selection:'on'});
            const pending=law.resolveDeclaredColor({theme,declaration,kind:'control',palette,operation:'pending'});
            const css=law.compileDeclaredColor({theme,declaration,kind:'control',palette,id:'semantic-action'});
            const repeat=law.compileDeclaredColor({theme,declaration,kind:'control',palette,id:'semantic-action'});
            out.push({theme,status:[rest,hover,pressed,focused,disabled,selected,pending,css].map(x=>x.status),
              deterministic:css.css===repeat.css,changes:rest.channels.background!==hover.channels.background,
              pressed:pressed.channels.background!==hover.channels.background,
              focus:focused.channels.focusRing!=='transparent',blocked:disabled.applied.blocked,
              selected:selected.applied.selected,pending:pending.applied.operating,
              browserCss:css.css.includes(':hover')&&css.css.includes(':active')&&css.css.includes(':focus-visible')});
          }
          const valid=law.validateColorDeclaration(declaration,'control');
          const refusals=[
            law.validateColorDeclaration({...declaration,foreground:'#ff0000'},'control').reason,
            law.validateColorDeclaration({...declaration,role:'action.made-up'},'control').reason,
            law.validateColorDeclaration({...declaration,surface:'text'},'control').reason,
            law.validateColorDeclaration({...declaration,consequence:'unknown'},'control').reason,
            law.validateColorDeclaration({role:'text.primary',surface:'text',consequence:'destructive'},'text').reason,
            law.validateColorDeclaration({...declaration,selection:'on',surface:'text'},'text').reason,
            law.validateColorDeclaration({...declaration},'invisible').reason
          ];
          return {schema:valid.schema,valid:valid.status,record:valid.semantic,out,refusals};
        }''')
        browser.close()
    assert result['valid']=='declared' and result['schema']=='mcel.semantic-color-declaration.v1'
    assert len(result['out'])==8
    for item in result['out']:
        assert item['status']==['resolved']*8, item
        assert all(item[k] for k in ['deterministic','changes','pressed','focus','blocked','selected','pending','browserCss']), item
    assert result['refusals']==[
        'unknown-color-declaration-field','unknown-role','control-surface-required',
        'unknown-consequence','consequence-on-non-action','selection-on-nonselectable-surface',
        'unknown-element-kind'
    ]


def test_official_dsl_transmits_only_semantic_color_declarations(tmp_path):
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    source=(ROOT/'mcel_apps/slh/network/application.js').read_text()
    original='fields:{address:text,port:integer,tlsSha256:text},defaults:{port:8899},intent:inspect'
    assert original in source
    changed=source.replace(original,
        original+',color:{role:"action.inspect",surface:"control",context:"form",consequence:"routine"}',1)
    authored=tmp_path/'application.js';authored.write_text(changed)
    report=compile_dsl_application(authored)
    assert report.valid,report.diagnostics
    nodes=report.normalized_ir['surfaces'][0]['nodes']
    form=next(node for node in nodes if node['nodeKind']=='form')
    assert form['color']=={'role':'action.inspect','surface':'control','context':'form','consequence':'routine'}
    invalid=tmp_path/'invalid.js'
    invalid.write_text(source.replace(original,original+',color:{role:"action.inspect",surface:"control",background:"red"}',1))
    rejected=compile_dsl_application(invalid)
    assert not rejected.valid
