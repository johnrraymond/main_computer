"""Positive/negative witnesses for the independent MCEL composition oracle."""
from __future__ import annotations

from copy import deepcopy
from playwright.sync_api import sync_playwright
from tools import mcel_viewport_composition_oracle as oracle
from tools import mcel_viewport_scroll_frob as scroll
from tools.mcel_phase2b_browser import launch_semantic_browser


def panel(id, purpose, *, w=220, h=760, y=0, overflow='auto', special='', content=False):
    return {'id':id,'purpose':purpose,'contentKind':'','scrollOwner':special,
            'isSpecialized':content,
            'rect':{'x':0,'y':y,'w':w,'h':h,'scrollHeight':h+1500,
                    'clientHeight':h,'overflowY':overflow}}


def good_observation(width=1040, height=768):
    compact=width<680
    nav=panel('nav','navigation',w=width if compact else 225,h=height)
    work=panel('work','workspace',w=width if compact else width-225,h=height)
    work['rect']['x']=0 if compact else 225
    return {'app':{'x':0,'y':0,'w':width,'h':height},
            'primary':work,'navigation':None if compact else nav,
            'panels':[], 'scrollRegions':[work] if compact else [nav,work],
            'viewport':{'width':width,'height':height}}


def test_dominant_workspace_and_full_height_navigation_pass():
    for width,height in ((390,650),(1040,768),(1440,900)):
        result=oracle.assess(good_observation(width,height))
        assert result['status']=='PASS',result
        assert all(result['checks'].values()),result


def test_equal_height_card_grid_rejected_even_when_outer_app_is_bounded():
    obs=good_observation()
    obs['panels']=[panel('card-'+str(i),'query-controls',w=200,h=170) for i in range(5)]
    result=oracle.assess(obs)
    assert result['status']=='FAIL',result
    assert 'no-equal-height-card-strips' in result['failures']
    assert 'unowned-scroll-region:card-0' not in result['failures']


def test_narrow_workspace_and_short_navigation_refused():
    obs=good_observation()
    obs['primary']['rect']['w']=210
    obs['primary']['rect']['h']=150
    obs['navigation']['rect']['h']=155
    result=oracle.assess(obs)
    for expected in ('primary-workspace-dominant-width','primary-workspace-usable-height',
                     'navigation-uses-allocated-height'):
        assert expected in result['failures'],result


def test_ordinary_card_cannot_become_scroll_owner_and_specialized_content_must_be_real():
    obs=good_observation()
    obs['scrollRegions'].append(panel('actions','commands',overflow='auto'))
    assert 'unowned-scroll-region:actions' in oracle.assess(obs)['failures']
    obs['scrollRegions'][-1]['scrollOwner']='editor'
    assert 'unowned-scroll-region:actions' in oracle.assess(obs)['failures']
    obs['scrollRegions'][-1]['isSpecialized']=True
    assert oracle.assess(obs)['status']=='PASS'
    obs['scrollRegions'][-1]['scrollOwner']='arbitrary'
    assert 'unowned-scroll-region:actions' in oracle.assess(obs)['failures']


def test_hidden_content_that_cannot_scroll_is_rejected():
    obs=good_observation()
    obs['scrollRegions'].append(panel('clipped-record','inspection',overflow='hidden'))
    result=oracle.assess(obs)
    assert 'clipped-ordinary-region:clipped-record' in result['failures'],result


def test_unnamed_or_unmounted_application_fails_closed():
    assert oracle.assess({'error':'semantic-application-not-mounted'})['status']=='FAIL'
    assert oracle.assess({'app':{},'viewport':{'width':1040}})['status']=='FAIL'


def _render_good_root(width):
    return '''<!doctype html><html><head><style>
      *,*::before,*::after{box-sizing:border-box}
      html,body{height:100%;margin:0;overflow:hidden}
      #mcel-workbench{height:100dvh;overflow:hidden}
      main{height:100%;display:flex;min-width:0;min-height:0;overflow:hidden}
      nav{width:23%;min-width:0;overflow:auto;min-height:0}
      section{flex:1;min-width:0;min-height:0;overflow:auto}
      article{padding:16px;border:1px solid #bbb}
      @media(max-width:680px){main{flex-direction:column}nav{display:none}section{height:100%}}
    </style></head><body><div id="mcel-workbench" data-mcel-semantic-operational="positive">
    <main data-mcel-region="application" data-mcel-purpose="application">
    <nav data-mcel-region="navigation" data-mcel-purpose="navigation">Menu</nav>
    <section data-mcel-region="workspace" data-mcel-purpose="workspace">
      <article><h2>Workspace</h2><p>Content flows in an ordinary card.</p></article>
    </section></main></div></body></html>'''


def _render_wrong_grid():
    panels=['<nav data-mcel-region="nav" data-mcel-purpose="navigation">Navigation</nav>',
        '<section data-mcel-region="work" data-mcel-purpose="workspace">Workspace</section>']
    panels += [f'<section data-mcel-region="card-{i}" data-mcel-purpose="commands">Actions</section>'
               for i in range(3)]
    return '''<!doctype html><html><head><style>
      *{box-sizing:border-box}html,body{height:100%;margin:0;overflow:hidden}
      #mcel-workbench{height:100dvh;overflow:hidden}
      main{height:100%;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));
        grid-auto-rows:minmax(0,1fr);overflow:hidden}
      main>*{overflow:auto}
    </style></head><body><div id="mcel-workbench" data-mcel-semantic-operational="bad">
    <main data-mcel-region="application" data-mcel-purpose="application">'''+''.join(panels)+'''</main>
    </div></body></html>'''


def test_real_chromium_accepts_full_height_scrollers_and_rejects_sliver_grid():
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            for width,height in ((390,650),(1040,768)):
                page=browser.new_page(viewport={'width':width,'height':height})
                page.set_content(_render_good_root(width))
                composed=oracle.assess(page.evaluate(oracle.DOM_OBSERVATION))
                assert composed['status']=='PASS',composed
                if width>=680:
                    bounded=page.evaluate(scroll.PROBE)
                    assert bounded['ok'],bounded
                page.close()
            page=browser.new_page(viewport={'width':1040,'height':768})
            page.set_content(_render_wrong_grid())
            bounded=page.evaluate(scroll.PROBE)
            assert bounded['ok'],bounded  # Old, insufficient oracle passes.
            # Restore the fixture: the old probe injected synthetic rows.
            page.set_content(_render_wrong_grid())
            composed=oracle.assess(page.evaluate(oracle.DOM_OBSERVATION))
            assert composed['status']=='FAIL',composed
            assert 'primary-workspace-dominant-width' in composed['failures']
            assert 'unowned-scroll-region:card-0' in composed['failures']
            page.close()
        finally:
            browser.close()


def test_oracle_fails_closed_on_new_mcel_app_without_host(tmp_path,monkeypatch):
    # Future packages cannot be accidentally omitted from all-app acceptance.
    from pathlib import Path
    fake=tmp_path/'mcel_apps'/'future-app'
    fake.mkdir(parents=True)
    (fake/'application.js').write_text('export const app = {};')
    monkeypatch.setattr(scroll,'ROOT',tmp_path)
    import pytest
    with pytest.raises(ValueError,match='lacks package host: future-app'):
        scroll.hosts()


def test_browser_gate_includes_chat_console_shell_and_every_current_semantic_host():
    expected={'slh-local','slh-network','file-manager','task-manager','code-editor',
              'calculator','generated-monitor','applications-shell','chat-console'}
    assert set(scroll.hosts())==expected
