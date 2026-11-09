"""Source-bound legacy-desktop-to-MCEL applications-shell parity oracle.

Causal mode checks a candidate in real Chromium without changing production.
Hosted mode checks the live canonical parent and explicit native child routes,
including persistence of the launcher through app navigation and reload.
Differences are reported, not corrected.
"""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

from main_computer.mcel_applications_shell_host import (
    ROOT, ROUTE, LEGACY_HTML, LEGACY_BASE, compile_applications_shell, legacy_desktop_reference,
    render_applications_shell_candidate,
)

SIZES = [390, 500, 1040, 1440]
OUTPUT = Path("runtime/reports/mcel-applications-shell-oracle")
# Snapshotted from the user's original desktop. Reject silent baseline changes.
PINNED_SOURCE_SHA256 = {
    'main_computer/web/applications/styles/base.css': '8d4bd1ed231f91161daf6b90dcdddccdda708e483293088fa0bd4c8e1ec43125',
    'main_computer/web/applications/scripts/dom-bindings/navigation.js': 'edacc7bf452be02c801714d6e0f320517e4f567eb0e35e5809b3583c3c4f8ee5',
    'main_computer/web/applications/scripts/desktop-icons.js': '18250db3c7e34651dba73d19e70a2b049f35a09713d5b04b37ff101a62329cd4',
    'main_computer/web/applications.html': 'c76610e8f05e120743b5127ea4622c9bbfcd9a0cf05b07e313338d622319bd61',
}


def legacy_fixture(ref: dict) -> str:
    """Reference renderer independent of the MCEL shell's markup/JS/CSS."""
    source = ref["css"]
    # Independent reference DOM assembly; does not call the candidate icon renderer.
    icons = []
    for record in ref["icons"]:
        slug = escape(record["app"], quote=True)
        glyph = ref["svg"][record["app"]]
        icons.append('<button class="desktop-icon" data-app="' + slug + '"><span class="desktop-glyph">'
                     + '<svg class="desktop-glyph-svg" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
                     + glyph + '</svg></span><strong>' + escape(record["title"]) + '</strong><span>'
                     + escape(record["summary"]) + '</span></button>')
    icons = "".join(icons)
    return f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>:root{{--ink:#f7f3e8;--muted:#c9c0aa;--gold:#f6c75b}}
    *{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%}}
    body{{font:14px/1.45 Arial,Helvetica,sans-serif;background:#10061d;color:#f7f3e8}}
    #reference-mount{{position:absolute;left:56px;right:0;top:124px;bottom:26px;overflow:hidden}}
    {source}</style></head><body><div id="reference-mount">
    <div class="desktop-overlay desktop-home" id="desktop-overlay" aria-label="Desktop application launcher">
    <div class="desktop-caption">Desktop applications</div>{icons}</div></div></body></html>'''


def legacy_launcher_fixture(repo: Path = ROOT) -> str:
    """Original launcher markup and CSS, never the candidate implementation."""
    html = (repo / LEGACY_HTML).read_text(encoding="utf-8")
    stylesheet = (repo / LEGACY_BASE).read_text(encoding="utf-8")
    match = re.search(r'<aside class="panel launcher"[\s\S]*?</aside>', html)
    if not match:
        raise RuntimeError("legacy-applications-launcher-missing")
    return ('<!doctype html><html lang="en"><head><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><style>'
            + stylesheet + '\n.launcher{transition:none!important}'
            + '</style></head><body data-has-app-taskbar="true">'
            + match.group(0) + '</body></html>')


_LAUNCHER_CAPTURE = r'''() => {
  const panel=document.querySelector('.launcher,.mcel-shell-launcher-panel');
  if (!panel) return {error:'launcher-panel-missing'};
  const bounds=panel.getBoundingClientRect(), style=getComputedStyle(panel);
  const headline=panel.querySelector('h2');
  const cards=[...panel.querySelectorAll('.app-card,.mcel-shell-app-card')];
  return {
    rect:{left:bounds.left,top:bounds.top,right:bounds.right,width:bounds.width,height:bounds.height},
    gap:style.gap, padding:style.padding, background:style.backgroundColor,
    overflowY:style.overflowY, headingFont:getComputedStyle(headline).font,
    cards:cards.map(el=>{
      const b=el.getBoundingClientRect(), cs=getComputedStyle(el);
      const strong=el.querySelector('strong'), summary=el.querySelector('span');
      return {id:el.dataset.app,title:strong?.textContent,summary:summary?.textContent,
        route:el.getAttribute('href'),font:cs.font,gap:cs.gap,padding:cs.padding,
        background:cs.backgroundColor,borderColor:cs.borderColor,shadow:cs.boxShadow,
        strongFont:getComputedStyle(strong).font,summaryFont:getComputedStyle(summary).font,
        rect:{x:b.left-bounds.left,y:b.top-bounds.top,w:b.width,h:b.height}};
    })
  };
}'''


def capture_launcher(page) -> dict:
    return page.evaluate(_LAUNCHER_CAPTURE)


def compare_launcher(reference: dict, actual: dict) -> list[str]:
    """Check full expanded launcher parity independent of shell masthead height."""
    errors=[]
    for name, item in (("reference", reference), ("candidate", actual)):
        if "error" in item:
            errors.append(name + ':' + item['error'])
    if errors:
        return errors
    if len(reference['cards']) != len(actual['cards']):
        return [f'launcher-card-count:{len(reference["cards"])}!={len(actual["cards"])}']
    for key in ('gap','padding','background','overflowY','headingFont'):
        if reference[key]!=actual[key]:
            errors.append('launcher.'+key+':changed')
    if abs(reference['rect']['width']-actual['rect']['width'])>1.5:
        errors.append('launcher.panel-width:changed')
    for n,(expected, candidate) in enumerate(zip(reference['cards'],actual['cards'])):
        if expected['id']!=candidate['id']:
            errors.append(f'launcher[{n}].id:changed')
        for key in ('title','summary','font','gap','padding','background','borderColor','shadow','strongFont','summaryFont'):
            if expected[key]!=candidate[key]:
                errors.append(f'launcher[{expected["id"]}].{key}:changed')
        for key in ('x','y','w','h'):
            if abs(expected['rect'][key]-candidate['rect'][key])>1.5:
                errors.append(f'launcher[{expected["id"]}].rect.{key}:changed')
    return errors


_CAPTURE = r'''() => {
  const grid=document.querySelector('.desktop-overlay.desktop-home');
  if (!grid) return {error: 'missing-desktop-overlay'};
  const r=grid.getBoundingClientRect();
  return {rect:{left:r.left,top:r.top,width:r.width,height:r.height},
    gap:getComputedStyle(grid).gap, columns:getComputedStyle(grid).gridTemplateColumns,
    captionFont:getComputedStyle(grid.querySelector('.desktop-caption')).font,
    captionColor:getComputedStyle(grid.querySelector('.desktop-caption')).color,
    icons:[...grid.querySelectorAll('.desktop-icon')].map(el=>{
      const b=el.getBoundingClientRect(), style=getComputedStyle(el);
      const glyph=el.querySelector('.desktop-glyph'), gs=glyph&&getComputedStyle(glyph);
      const gb=glyph&&glyph.getBoundingClientRect();
      return {id:el.dataset.app,title:el.querySelector('strong')?.textContent,
        summary:[...el.children].filter(x=>x.tagName==='SPAN'&&!x.classList.contains('desktop-glyph'))[0]?.textContent,
        svg:el.querySelector('svg')?.innerHTML.trim()||null,
        accent:style.getPropertyValue('--desktop-icon-accent').trim(),
        background:style.backgroundImage, border:style.borderColor, borderRadius:style.borderRadius,
        titleFont:getComputedStyle(el.querySelector('strong')).font,
        summaryFont:getComputedStyle([...el.children].filter(x=>x.tagName==='SPAN'&&!x.classList.contains('desktop-glyph'))[0]).font,
        glyphBackground:gs?.backgroundImage, glyphColor:gs?.color,
        glyphSize:gb?{w:gb.width,h:gb.height}:null,
        rect:{x:b.x-r.x,y:b.y-r.y,w:b.width,h:b.height}};
    })};
}'''


def capture(page):
    return page.evaluate(_CAPTURE)


def compare(reference: dict, actual: dict) -> list[str]:
    errors=[]
    if "error" in actual:
        return [actual["error"]]
    if len(reference["icons"]) != len(actual.get("icons", [])):
        errors.append(f'icon-count:{len(reference["icons"])}!={len(actual.get("icons",[]))}')
        return errors
    for idx, (expected, candidate) in enumerate(zip(reference["icons"], actual["icons"])):
        if expected["id"] != candidate["id"]:
            errors.append(f'icon-order[{idx}]:{expected["id"]}!={candidate["id"]}')
        for key in ("title", "summary", "svg", "accent", "background", "border", "borderRadius", "glyphBackground", "glyphColor", "glyphSize", "titleFont", "summaryFont"):
            if expected[key] != candidate[key]:
                errors.append(f'icon[{expected["id"]}].{key}: changed')
        for k in ("x", "y", "w", "h"):
            if abs(expected["rect"][k] - candidate["rect"][k]) > 1.5:
                errors.append(f'icon[{expected["id"]}].rect.{k}:{expected["rect"][k]:.1f}!={candidate["rect"][k]:.1f}')
    if abs(reference["rect"]["width"] - actual["rect"]["width"]) > 1.5:
        errors.append("desktop-grid-width-mismatch")
    if abs(reference["rect"]["height"] - actual["rect"]["height"]) > 1.5:
        errors.append("desktop-grid-height-mismatch")
    if reference["gap"] != actual["gap"]:
        errors.append("desktop-grid-gap-mismatch")
    for key in ('captionFont','captionColor'):
        if reference[key]!=actual[key]:
            errors.append('desktop-' + key + '-mismatch')
    return errors


def navigation_link_errors(links: dict, registry: dict, icon_ids: set[str]) -> list[str]:
    """A semantic destination requires a real canonical href in both launchers.

    A button that happens to navigate when clicked is insufficient: it cannot
    be independently copied, opened in another window, or bookmarked.
    """
    routes = {entry['id']: entry['route'] for entry in registry['apps']}
    failures = []
    for collection, expected in (('icons', icon_ids), ('launcher', set(routes))):
        found = links.get(collection)
        if not isinstance(found, list) or len(found) != len(expected):
            failures.append(collection + '-navigation-inventory-invalid')
            continue
        seen = set()
        for item in found:
            app_id = item.get('app') if isinstance(item, dict) else None
            if app_id not in expected or app_id in seen:
                failures.append(collection + '-navigation-identity-invalid')
                continue
            seen.add(app_id)
            if (item.get('tag') != 'A' or item.get('href') != routes[app_id] or
                item.get('destination') != 'applications.' + app_id):
                failures.append(collection + '-canonical-navigation-link-invalid:' + app_id)
        if seen != expected:
            failures.append(collection + '-navigation-inventory-incomplete')
    return failures


def canonical_mount_errors(*, parent_url: str, expected_route: str, child_src: str | None,
                           promoted: str | None, active_app: str | None, rail_count: int) -> list[str]:
    """Validate a production shell navigation, not the opt-in preview route.

    Parent URLs stay canonical; only the iframe is marked as a native child.
    A flag on the parent or a missing flag on the child is a failed promotion.
    """
    errors = []
    parsed = urlsplit(parent_url)
    if parsed.path != expected_route or parsed.query or parsed.fragment:
        errors.append("route-navigation-escaped-shell")
    if child_src != expected_route + "?mcel-shell-child=1":
        errors.append("route-navigation-child-not-canonical")
    if promoted != "true":
        errors.append("route-navigation-shell-not-promoted")
    if active_app != expected_route.removeprefix('/applications/'):
        errors.append("route-navigation-active-app-mismatch")
    if rail_count != 1:
        errors.append("route-navigation-lost-apps-rail")
    return errors


def _observed_canonical_mount(page, expected_route: str) -> list[str]:
    return canonical_mount_errors(
        parent_url=page.url,
        expected_route=expected_route,
        child_src=page.locator('#mcel-shell-frame').get_attribute('src'),
        promoted=page.locator('body').get_attribute('data-mcel-shell-canonical-promoted'),
        active_app=page.locator('body').get_attribute('data-mcel-shell-active-app'),
        rail_count=page.locator('#mcel-shell-launcher-panel').count(),
    )


def run(*, mode: str = "causal", host_url: str = "http://127.0.0.1:8765", screenshot: bool = False,
        output: Path | None = None) -> dict:
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    output = output or ROOT / OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    ref = legacy_desktop_reference()
    _, reg = compile_applications_shell()
    reference_drift = {path: {'expected':sha,'actual':ref['source_sha256'].get(path)}
                       for path,sha in PINNED_SOURCE_SHA256.items()
                       if ref['source_sha256'].get(path)!=sha}
    results=[]
    with sync_playwright() as browser_api:
        browser = browser_api.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        try:
            for width in SIZES:
                context = browser.new_context(viewport={"width":width,"height":840}, reduced_motion="reduce")
                reference_page = context.new_page()
                launcher_page = context.new_page()
                candidate_page = context.new_page()
                failures=[]
                if reference_drift:
                    failures.append('reference-source-fingerprint-changed')
                script_errors=[]
                candidate_page.on("pageerror", lambda e: script_errors.append(str(e)))
                reference_page.set_content(legacy_fixture(ref),wait_until="load")
                launcher_page.set_content(legacy_launcher_fixture(),wait_until="load")
                if mode == "hosted":
                    candidate_page.goto(host_url.rstrip("/") + ROUTE,wait_until="domcontentloaded",timeout=20000)
                else:
                    candidate_page.set_content(render_applications_shell_candidate(),wait_until="load")
                reference = capture(reference_page)
                actual = capture(candidate_page)
                failures.extend(compare(reference, actual))
                # A browser-native URL is part of the semantic destination
                # contract, not an optional JavaScript click effect.
                navigation_links = candidate_page.evaluate("""() => ({
                  icons:[...document.querySelectorAll('#mcel-shell-desktop-overlay .desktop-icon')]
                    .map(e=>({app:e.dataset.app,tag:e.tagName,href:e.getAttribute('href'),
                      destination:e.dataset.mcelDestinationId})),
                  launcher:[...document.querySelectorAll('.mcel-shell-app-card')]
                    .map(e=>({app:e.dataset.app,tag:e.tagName,href:e.getAttribute('href'),
                      destination:e.dataset.mcelDestinationId}))
                })""")
                failures.extend(navigation_link_errors(
                    navigation_links, reg, {entry['app'] for entry in ref['icons']}))
                # A fresh-page hover isn't enough. Reproduce the reported
                # desktop-icon -> semantic app -> FIRST rail hover sequence.
                # Causal mode runs in about:blank: stub only the URL write,
                # never the click handler, hover state, or DOM geometry.
                candidate_page.evaluate("async () => await MCELDiagnosticPlugins.enable('mcel-shell-launcher')")
                if mode != 'hosted':
                    candidate_page.evaluate('history.pushState = function () {}')
                candidate_page.mouse.move(width - 12, 410)
                candidate_page.locator('.desktop-icon[data-app="chat-console"]').click()
                if candidate_page.locator('body').get_attribute('data-mcel-shell-active-app') != 'chat-console':
                    failures.append('desktop-icon-did-not-select-chat-console')
                dismissed_after_navigation = candidate_page.locator('#mcel-shell-launcher-panel').get_attribute('data-dismissed')
                if dismissed_after_navigation == 'true':
                    failures.append('launcher-dismissed-without-being-opened')
                candidate_page.mouse.move(20, 360)
                candidate_page.wait_for_timeout(300)
                first_hover_left = candidate_page.locator('#mcel-shell-launcher-panel').evaluate(
                    '(el) => el.getBoundingClientRect().left')
                if abs(first_hover_left) > 3:
                    failures.append(f'first-hover-after-navigation-did-not-open:{first_hover_left}')
                diagnostic = candidate_page.evaluate('''async () => (await MCELDiagnosticPlugins.run('mcel-shell-launcher')).result''')
                diagnostic_kinds = [event['kind'] for event in diagnostic['events']]
                if 'first-pointer-enter' not in diagnostic_kinds or 'first-hover-expanded' not in diagnostic_kinds:
                    failures.append('launcher-diagnostic-did-not-record-first-hover')
                if 'first-hover-did-not-expand' in diagnostic_kinds:
                    failures.append('launcher-diagnostic-reported-first-hover-failure')
                candidate_page.mouse.move(width-12, 410)
                candidate_page.evaluate('window.__mcelApplicationsShell.select("desktop", false)')
                # Compare source-controlled legacy launcher with candidate
                # relative card geometry (the host mastheads differ in height).
                launcher_page.mouse.move(20, 350)
                candidate_page.mouse.move(20, 350)
                original_launcher = capture_launcher(launcher_page)
                candidate_launcher = capture_launcher(candidate_page)
                failures.extend(compare_launcher(original_launcher, candidate_launcher))
                if abs(original_launcher['rect']['left']) > 3 or abs(candidate_launcher['rect']['left']) > 3:
                    failures.append('launcher-hover-open-failed')
                # Hosted acceptance also checks the *running legacy desktop*.
                if mode == 'hosted':
                    live_legacy = context.new_page()
                    live_legacy.goto(host_url.rstrip('/') + '/applications?mcel-shell-child=1', wait_until='domcontentloaded', timeout=30000)
                    live_legacy.locator('#desktop-overlay .desktop-icon').first.wait_for(timeout=25000)
                    legacy = capture(live_legacy)
                    if 'error' in legacy or len(legacy.get('icons', [])) != 20:
                        failures.append('hosted-legacy-icon-inventory-invalid')
                    else:
                        # Compare exact identity / SVG / colors, not bounding boxes:
                        # canonical apps and their legacy desktop have different host chrome.
                        for a,b in zip(reference['icons'], legacy['icons']):
                            for key in ('id','title','summary','svg','accent'):
                                if a.get(key) != b.get(key):
                                    failures.append(f'hosted-legacy.{key}:{a.get("id")}')
                    live_legacy.close()
                    live_promoted = context.new_page()
                    for promoted_path, expected_app in [('/applications', 'desktop'),
                                                        ('/applications/chat-console', 'chat-console'),
                                                        ('/applications/document', 'document')]:
                        live_promoted.goto(host_url.rstrip('/') + promoted_path,
                                           wait_until='domcontentloaded', timeout=30000)
                        if (live_promoted.locator('body').get_attribute('data-mcel-shell-canonical-promoted') != 'true'
                            or live_promoted.locator('body').get_attribute('data-mcel-shell-active-app') != expected_app
                            or live_promoted.locator('#mcel-shell-launcher-panel').count() != 1):
                            failures.append('hosted-canonical-shell-not-promoted:' + promoted_path)
                        if expected_app != 'desktop':
                            expected_src = promoted_path + '?mcel-shell-child=1'
                            if live_promoted.locator('#mcel-shell-frame').get_attribute('src') != expected_src:
                                failures.append('hosted-child-route-not-explicit:' + promoted_path)
                    live_promoted.close()
                if candidate_page.locator('body').get_attribute('data-mcel-shell-ready') != 'true':
                    failures.append('shell-not-ready')
                if candidate_page.locator('.mcel-shell-app-card').count() != len(reg['apps']):
                    failures.append('launcher-inventory-mismatch')
                if candidate_page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'):
                    failures.append('horizontal-overflow')
                # Must work independently of an "Apps" button; hover is the original interaction.
                panel=candidate_page.locator('#mcel-shell-launcher-panel')
                candidate_page.mouse.move(width-12, 410)
                collapsed = candidate_page.evaluate('''() => {
                    const r=document.querySelector('#mcel-shell-launcher-panel').getBoundingClientRect();
                    return {right:r.right,width:r.width,left:r.left};}''')
                if abs(collapsed["right"] - 56) > 3:
                    failures.append(f'collapsed-rail-right:{collapsed["right"]}')
                # The original launcher opens on rail hover, without requiring click.
                candidate_page.mouse.move(20, 360)
                hovered=candidate_page.evaluate('''() =>
                    document.querySelector('#mcel-shell-launcher-panel').getBoundingClientRect().left''')
                if abs(hovered) > 3:
                    failures.append(f'rail-hover-did-not-open:{hovered}')
                candidate_page.mouse.move(width-12, 410)
                closed=candidate_page.evaluate('''() =>
                    document.querySelector('#mcel-shell-launcher-panel').getBoundingClientRect().right''')
                if abs(closed-56)>3:
                    failures.append(f'rail-hover-did-not-collapse:{closed}')
                candidate_page.get_by_role('button',name='Apps',exact=True).click()
                expanded=candidate_page.evaluate('''() => {
                    const r=document.querySelector('#mcel-shell-launcher-panel').getBoundingClientRect();
                    return {right:r.right,width:r.width,left:r.left};}''')
                if abs(expanded["left"]) > 3:
                    failures.append(f'expanded-launcher-left:{expanded["left"]}')
                if candidate_page.locator('.mcel-shell-app-card').count() != 21:
                    failures.append('launcher-card-count')
                # Focus must open the launcher, Escape must actually close it.
                candidate_page.locator('.mcel-shell-app-card[data-app="calculator"]').focus()
                focus_left = panel.evaluate('(el) => el.getBoundingClientRect().left')
                if abs(focus_left) > 3:
                    failures.append('launcher-keyboard-focus-did-not-open')
                candidate_page.keyboard.press('Escape')
                escape_right = panel.evaluate('(el) => el.getBoundingClientRect().right')
                if abs(escape_right - 56) > 3:
                    failures.append('launcher-escape-did-not-close')
                if mode == "hosted":
                    candidate_page.get_by_role('button',name='Apps',exact=True).click()
                    candidate_page.locator('.mcel-shell-app-card[data-app="chat-console"]').click()
                else:
                    # about:blank forbids pushState. Exercise the same mount without URL mutation.
                    candidate_page.evaluate('window.__mcelApplicationsShell.select("chat-console",false)')
                if candidate_page.locator('body').get_attribute('data-mcel-shell-active-app') != 'chat-console':
                    failures.append('semantic-navigation-failed')
                if candidate_page.locator('#mcel-shell-frame').get_attribute('src') != '/applications/chat-console?mcel-shell-child=1':
                    failures.append('canonical-child-route-lost')
                if mode == 'hosted':
                    # A src attribute is not evidence that a promoted application
                    # actually loaded. Verify the same-origin child document and
                    # the existing Chat Console semantic runtime contract.
                    try:
                        candidate_page.wait_for_function("""() => {
                          const f=document.querySelector('#mcel-shell-frame');
                          try {return f.contentWindow.location.pathname === '/applications/chat-console'
                            && !!f.contentDocument.body && f.contentDocument.readyState !== 'loading'
                            && f.contentDocument.body.dataset.mcelChatReady === 'true';}
                          catch (_e) {return false;}
                        }""", timeout=30000)
                        chat_contract=candidate_page.evaluate("""() => {
                          const d=document.querySelector('#mcel-shell-frame').contentDocument;
                          return d.querySelector('#chat-console-app')?.getAttribute('data-mcel-chat-contract');
                        }""")
                        if chat_contract != 'mcel.chat-console.composition.v1':
                            failures.append('hosted-chat-console-contract-mismatch')
                    except BrowserTimeout:
                        failures.append('hosted-chat-console-child-did-not-initialize')
                # Exercise the actual promoted URLs, not the opt-in preview.
                # The parent must retain a single launcher and a canonical URL;
                # the child must carry ?mcel-shell-child=1 to avoid recursion.
                route_continuity = None
                if mode == 'hosted':
                    route_page = context.new_page()
                    try:
                        route_page.goto(host_url.rstrip('/') + '/applications/chat-console',
                                        wait_until='domcontentloaded', timeout=25000)
                        rail = route_page.locator('#mcel-shell-launcher-panel')
                        rail.wait_for(timeout=15000)
                        if route_page.locator('body').get_attribute('data-mcel-shell-ready') != 'true':
                            failures.append('canonical-route-shell-not-ready')
                        failures.extend('initial:' + e for e in _observed_canonical_mount(
                            route_page, '/applications/chat-console'))
                        route_page.evaluate('window.__mcelOraclePersistentRail = document.getElementById("mcel-shell-launcher-panel")')
                        route_page.get_by_role('button', name='Apps', exact=True).click()
                        route_page.locator('.mcel-shell-app-card[data-app="calculator"]').click()
                        failures.extend(_observed_canonical_mount(route_page, '/applications/calculator'))
                        if not route_page.evaluate('window.__mcelOraclePersistentRail === document.getElementById("mcel-shell-launcher-panel")'):
                            failures.append('route-navigation-replaced-apps-rail')
                        # Verify the child document really loads as native
                        # Calculator instead of recursively becoming a shell.
                        try:
                            route_page.wait_for_function("""() => {
                              const f=document.getElementById('mcel-shell-frame');
                              try {return f?.contentWindow?.location?.pathname === '/applications/calculator'
                                && f.contentWindow.location.search === '?mcel-shell-child=1'
                                && !!f.contentDocument.querySelector('#mcel-expanded-ir')
                                && !f.contentDocument.querySelector('#mcel-shell-launcher-panel');}
                              catch (_) {return false;}
                            }""", timeout=30000)
                        except BrowserTimeout:
                            failures.append('route-navigation-native-calculator-not-loaded')
                        route_page.reload(wait_until='domcontentloaded')
                        if route_page.locator('body').get_attribute('data-mcel-shell-ready') != 'true':
                            failures.append('route-reload-shell-not-ready')
                        failures.extend('reload:' + e for e in _observed_canonical_mount(
                            route_page, '/applications/calculator'))
                        route_page.mouse.move(width-8,400)
                        route_page.mouse.move(20,360)
                        rail_left = route_page.locator('#mcel-shell-launcher-panel').evaluate(
                            '(el) => el.getBoundingClientRect().left')
                        if abs(rail_left) > 3:
                            failures.append(f'route-reload-rail-would-not-expand:{rail_left}')
                        route_continuity = {
                            'path': route_page.url,
                            'rail_left': rail_left,
                            'shell_ready': route_page.locator('body').get_attribute('data-mcel-shell-ready'),
                            'canonical_promoted': route_page.locator('body').get_attribute('data-mcel-shell-canonical-promoted'),
                            'active_app': route_page.locator('body').get_attribute('data-mcel-shell-active-app'),
                            'child_src': route_page.locator('#mcel-shell-frame').get_attribute('src'),
                        }
                    finally:
                        route_page.close()
                # The native child is an iframe, but shell remains the sole app launcher.
                if script_errors:
                    failures.extend('pageerror:' + e for e in script_errors)
                if screenshot:
                    reference_page.screenshot(path=str(output/f'reference-{width}.png'),full_page=False)
                    launcher_page.screenshot(path=str(output/f'launcher-reference-{width}.png'),full_page=False)
                    # Go back to desktop and expand for an inspectable candidate screenshot.
                    candidate_page.evaluate('window.__mcelApplicationsShell.select("desktop",false)')
                    candidate_page.screenshot(path=str(output/f'candidate-{width}.png'),full_page=False)
                results.append({"width":width,"pass":not failures,"errors":failures,
                                "reference_icons":len(reference.get("icons",[])),
                                "candidate_icons":len(actual.get("icons",[])),
                                "launcher_collapsed":collapsed,"launcher_expanded":expanded,
                                "launcher_card_count":len(candidate_launcher.get('cards', [])),
                                "route_continuity":route_continuity,
                                "first_app_hover":{"left":first_hover_left,
                                    "dismissed_after_navigation":dismissed_after_navigation,
                                    "diagnostic_kinds":diagnostic_kinds}})
                reference_page.close();launcher_page.close();candidate_page.close();context.close()
        finally:
            browser.close()
    candidate_sources = (
        "main_computer/web/applications/scripts/mcel-applications-shell.js",
        "main_computer/web/applications/scripts/mcel-applications-shell-launcher-diagnostic.js",
        "main_computer/mcel_applications_shell_host.py",
    )
    candidate_fingerprints = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in candidate_sources
    }
    outcome = {"schema":"mcel.applications-shell.oracle.v1","mode":mode,
               "scope":("canonical-hosted-shell-with-native-child-routing" if mode == "hosted"
                        else "candidate-source-parity"),
               "production_promoted":(mode == "hosted" and all(
                   x['pass'] and x['route_continuity'] is not None and
                   x['route_continuity']['canonical_promoted'] == 'true' and
                   x['route_continuity']['child_src'] == '/applications/calculator?mcel-shell-child=1'
                   for x in results)),
               "reference_drift":reference_drift,
               "status":"PASS" if all(x['pass'] for x in results) else "FAIL",
               "reference_source_sha256":ref['source_sha256'],
               "candidate_source_sha256":candidate_fingerprints,
               "reference_app_count":len(ref['icons']),"results":results}
    (output/'report.json').write_text(json.dumps(outcome,indent=2)+'\n',encoding='utf-8')
    return outcome


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['causal','hosted'],default='causal')
    parser.add_argument('--host-url',default='http://127.0.0.1:8765')
    parser.add_argument('--screenshots',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=run(mode=args.mode,host_url=args.host_url,screenshot=args.screenshots,output=args.output)
    for row in result['results']:
        print(f'MCEL_SHELL_ORACLE {row["width"]}px {"PASS" if row["pass"] else "FAIL"} '
              f'icons={row["candidate_icons"]}/{row["reference_icons"]}')
        for reason in row['errors'][:15]: print('  '+reason)
    print(f'MCEL_APPLICATIONS_SHELL_ORACLE {result["status"]}')
    print('  report:',str(args.output or ROOT/OUTPUT/'report.json'))
    return 0 if result['status']=='PASS' else 1

if __name__=='__main__':
    raise SystemExit(main())
