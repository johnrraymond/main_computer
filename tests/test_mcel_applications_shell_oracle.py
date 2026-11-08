"""Regression oracle for MCEL shell migration of the legacy desktop icon grid."""
from copy import deepcopy
from pathlib import Path, PureWindowsPath

from main_computer.mcel_applications_shell_host import legacy_desktop_reference, render_applications_shell_candidate, _portable_repo_key
from tools import mcel_applications_shell_oracle as oracle


def _sample():
    item = {'id':'calculator','title':'Calculator','summary':'arithmetic tool',
            'svg':'<path d="M1 2"></path>', 'accent':'#ff6b6b', 'background':'x',
            'border':'rgb(0, 0, 0)', 'borderRadius':'12px','glyphBackground':'y',
            'glyphColor':'rgb(1, 2, 3)', 'glyphSize':{'w':52,'h':52},
            'rect':{'x':0,'y':0,'w':200,'h':144}, 'titleFont':'700 17px Arial', 'summaryFont':'13px Arial'}
    return {'icons':[item], 'rect':{'width':1000,'height':600}, 'gap':'18px', 'captionFont':'700 12px Arial','captionColor':'rgb(246, 199, 91)'}


def test_original_reference_is_pinned_and_has_all_glyphs():
    source=legacy_desktop_reference()
    assert source['source_sha256'] == oracle.PINNED_SOURCE_SHA256
    assert len(source['icons']) == 20
    assert len(source['svg']) == 20
    assert [x['app'] for x in source['icons']][0:3] == ['webgl','astrometric','calculator']
    assert source['svg']['calculator'].count('<path') >= 1


def test_reference_fixture_does_not_reuse_candidate_runtime():
    source=legacy_desktop_reference()
    html=oracle.legacy_fixture(source)
    assert html.count('class="desktop-icon"') == 20
    assert 'mcel-applications-shell' not in html
    assert 'data-shell-launch=' not in html


def test_candidate_restores_original_icons_without_promoting_route():
    html=render_applications_shell_candidate()
    assert html.count('class="desktop-icon"') == 20
    assert 'data-shell-launch="calculator"' in html
    assert 'data-shell-launch="chat-console"' in html
    assert 'class="desktop-overlay desktop-home"' in html
    assert 'id="mcel-shell-frame"' in html
    assert 'href="/applications"' in html
    assert '.desktop-icon[data-app="calculator"]' in html


def test_negative_control_missing_icon_fails():
    expected=_sample(); actual=deepcopy(expected)
    actual['icons'] = []
    assert any('icon-count' in x for x in oracle.compare(expected,actual))


def test_negative_control_changed_svg_fails():
    expected=_sample(); actual=deepcopy(expected)
    actual['icons'][0]['svg'] = '<path d="tampered"></path>'
    assert any('.svg:' in x for x in oracle.compare(expected,actual))


def test_negative_control_changed_color_fails():
    expected=_sample(); actual=deepcopy(expected)
    actual['icons'][0]['accent'] = '#000000'
    assert any('.accent:' in x for x in oracle.compare(expected,actual))


def test_negative_control_shifted_geometry_fails():
    expected=_sample(); actual=deepcopy(expected)
    actual['icons'][0]['rect']['x'] += 20
    assert any('.rect.x:' in x for x in oracle.compare(expected,actual))


def test_browser_source_to_shell_parity_at_four_viewports(tmp_path):
    report=oracle.run(mode='causal',output=tmp_path)
    assert report['status'] == 'PASS', report
    assert [r['width'] for r in report['results']] == [390,500,1040,1440]
    assert all(r['reference_icons'] == r['candidate_icons'] == 20 for r in report['results'])
    assert all(abs(r['first_app_hover']['left']) <= 3 for r in report['results'])
    assert all(r['first_app_hover']['dismissed_after_navigation'] != 'true' for r in report['results'])
    assert all('first-hover-expanded' in r['first_app_hover']['diagnostic_kinds'] for r in report['results'])
    assert report['candidate_source_sha256']['main_computer/web/applications/scripts/mcel-applications-shell.js']
    assert (tmp_path/'report.json').is_file()


def test_original_launcher_reference_is_not_a_candidate_clone():
    html = oracle.legacy_launcher_fixture()
    assert 'class="panel launcher"' in html
    assert html.count('class="app-card ready') == 21
    assert 'href="/applications/chat-console"' in html
    assert 'mcel-shell-app-card' not in html
    assert 'mcel-applications-shell' not in html


def test_negative_control_launcher_card_loss_or_type_drift_fails():
    baseline = {'cards':[{'id':'desktop','title':'Desktop','summary':'desktop app grid',
        'font':'16px Arial','gap':'6px','padding':'10px 14px',
        'background':'rgb(10, 10, 8)','borderColor':'rgb(1, 2, 3)','shadow':'none',
        'strongFont':'700 18px Arial','summaryFont':'16px Arial',
        'rect':{'x':30,'y':48,'w':255,'h':92}}],
        'rect':{'width':300},'gap':'12px','padding':'14px',
        'background':'rgb(17, 17, 14)','overflowY':'auto','headingFont':'700 18px Arial'}
    assert oracle.compare_launcher(baseline,deepcopy(baseline)) == []
    missing = deepcopy(baseline)
    missing['cards'] = []
    assert any('launcher-card-count' in e for e in oracle.compare_launcher(baseline,missing))
    resized = deepcopy(baseline)
    resized['cards'][0]['rect']['w'] -= 25
    assert any('.rect.w' in e for e in oracle.compare_launcher(baseline,resized))
    changed_type = deepcopy(baseline)
    changed_type['cards'][0]['summaryFont'] = '14px Arial'
    assert any('.summaryFont' in e for e in oracle.compare_launcher(baseline,changed_type))


def test_browser_desktop_icons_mount_semantic_apps_and_escape_restores_rail():
    from playwright.sync_api import sync_playwright
    import shutil
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page(viewport={'width':500,'height':840}, reduced_motion='reduce')
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.set_content(render_applications_shell_candidate(), wait_until='load')
            # A causal browser page cannot change about:blank history. Record
            # the requested navigation, while exercising real click handlers.
            page.evaluate('''() => {
                window.__shellPushed = [];
                history.pushState = function(_state, _title, url) {
                    window.__shellPushed.push(url);
                };
            }''')
            for app in ('calculator','file-explorer','task-manager','code-editor','chat-console'):
                page.locator(f'.desktop-icon[data-app="{app}"]').click()
                assert page.locator('body').get_attribute('data-mcel-shell-active-app') == app
                assert page.locator('#mcel-shell-frame').get_attribute('src') == '/applications/' + app + '?mcel-shell-child=1'
                assert page.locator('.mcel-shell-app-card[aria-current="page"]').get_attribute('data-app') == app
                assert page.evaluate('window.__shellPushed.at(-1)').endswith('?app=' + app)
                assert page.evaluate('window.__mcelApplicationsShell.select("desktop", false)') is True
            page.get_by_role('button',name='Apps',exact=True).click()
            panel=page.locator('#mcel-shell-launcher-panel')
            page.locator('.mcel-shell-app-card[data-app="calculator"]').focus()
            assert abs(panel.evaluate('(el)=>el.getBoundingClientRect().left')) < 3
            page.keyboard.press('Escape')
            assert panel.get_attribute('data-open') == 'false'
            assert abs(panel.evaluate('(el)=>el.getBoundingClientRect().right')-56) < 3
            assert page.evaluate('document.activeElement.id') == 'mcel-shell-open-apps'
            page.get_by_role('button',name='Apps',exact=True).click()
            page.locator('.mcel-shell-app-card[data-app="chat-console"]').click()
            assert page.locator('body').get_attribute('data-mcel-shell-active-app') == 'chat-console'
            assert abs(panel.evaluate('(el)=>el.getBoundingClientRect().right')-56) < 3
            assert not errors, errors
        finally:
            browser.close()


def test_reference_keys_portable_across_windows_and_posix():
    for raw_key in oracle.PINNED_SOURCE_SHA256:
        assert _portable_repo_key(Path(raw_key)) == raw_key
        assert _portable_repo_key(PureWindowsPath(raw_key)) == raw_key
    assert legacy_desktop_reference()["source_sha256"] == oracle.PINNED_SOURCE_SHA256


def test_first_hover_diagnostic_finds_stuck_dismissal_negative_control():
    """Negative control: the diagnostic must observe a deliberately blocked hover."""
    import shutil
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page(viewport={'width':1040,'height':840}, reduced_motion='reduce')
            page.set_content(render_applications_shell_candidate(),wait_until='load')
            page.evaluate('async () => await MCELDiagnosticPlugins.enable("mcel-shell-launcher")')
            page.mouse.move(900,400)
            page.evaluate('window.__mcelApplicationsShell.select("chat-console",false)')
            page.evaluate('document.querySelector("#mcel-shell-launcher-panel").dataset.dismissed="true"')
            page.mouse.move(20,360)
            page.wait_for_timeout(310)
            result = page.evaluate('async () => (await MCELDiagnosticPlugins.run("mcel-shell-launcher")).result')
            assert any(e['kind'] == 'first-hover-did-not-expand' for e in result['events']), result
            findings = page.evaluate('MCELDiagnosticPlugins.findings().filter(e => e.pluginId === "mcel-shell-launcher")')
            assert any(f['code'] == 'first-hover-did-not-expand' and f['severity'] == 'error' for f in findings)
        finally:
            browser.close()


def test_launcher_diagnostic_plugin_records_suppression_without_telemetry():
    """The script is in the candidate page and records no app contents."""
    page = render_applications_shell_candidate()
    assert 'id: "mcel-shell-launcher"' in page
    assert 'mcel-shell-launcher-trace' in page
    assert 'first-hover-did-not-expand' in page
    assert 'localOnly: true' in page
    assert 'root.__mcelApplicationsShell' in page


def test_canonical_route_contract_keeps_parent_clean_and_child_explicit():
    check=oracle.canonical_mount_errors
    good=dict(parent_url='http://127.0.0.1:8765/applications/calculator',
              expected_route='/applications/calculator',
              child_src='/applications/calculator?mcel-shell-child=1',
              promoted='true',active_app='calculator',rail_count=1)
    assert check(**good) == []
    old_child={**good,'child_src':'/applications/calculator'}
    assert 'route-navigation-child-not-canonical' in check(**old_child)
    old_preview={**good,'parent_url':good['parent_url']+'?mcel-shell-preview=1'}
    assert 'route-navigation-escaped-shell' in check(**old_preview)
    not_promoted={**good,'promoted':'false'}
    assert 'route-navigation-shell-not-promoted' in check(**not_promoted)
    recursive={**good,'child_src':'/applications/calculator?mcel-shell-preview=1'}
    assert 'route-navigation-child-not-canonical' in check(**recursive)
    lost_rail={**good,'rail_count':0}
    assert 'route-navigation-lost-apps-rail' in check(**lost_rail)
