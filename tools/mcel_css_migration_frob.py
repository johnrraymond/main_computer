"""Pixel-exact MCEL CSS -> canonical Theme/Form/Chrome migration acceptance.

Additive companion to mcel_css_retirement_frob.py. Test a candidate patch ZIP
against the running repository WITHOUT writing any repository file:

    python tools/mcel_css_migration_frob.py --candidate-zip candidate.zip --all-views

Candidate ZIP must contain deletion(s) in the workbench CSS and changed source
in one or more existing canonical presentation laws. Each patched asset is
substituted *in memory* in the actual rendered MCEL application page; no new
stylesheet, duplicate CSS override, or mock presentation implementation is used.

An exact full-page screenshot match is required over every stable exercised
state. Inactive laws, absent assets, UI errors and unstable original baselines
are failures/inconclusive, not migration successes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import io
import json
import sys
from pathlib import Path
from zipfile import ZipFile, BadZipFile

REPO_DIR = Path(__file__).resolve().parents[1]
if str(REPO_DIR) not in sys.path: sys.path.insert(0, str(REPO_DIR))

from tools.mcel_css_retirement_frob import (
    ROOT, STATES, _app_html, _capture, _pixel_diff, _state,
)

STYLE = "main_computer/web/applications/styles/mcel-semantic-workbench.css"
LAW_PATHS = {
    "main_computer/web/applications/scripts/mcel-style-law.js": "Theme",
    "main_computer/web/applications/scripts/mcel-semantic-color-presentation.js": "Theme",
    "main_computer/web/applications/scripts/mcel-semantic-theme-preview.js": "Theme",
    "main_computer/web/applications/scripts/mcel-semantic-form-presentation-law.js": "Form",
    "main_computer/web/applications/scripts/mcel-semantic-chrome-presentation-law.js": "Chrome",
}
APPS = ("file-manager", "task-manager", "code-editor", "slh")
# SLH does not embed the canonical Theme/Color assets in the available host;
# it must be opted in explicitly, and cannot pass until that integration exists.
DEFAULT_APPS = ("file-manager", "task-manager", "code-editor")


class MigrationRefused(ValueError):
    pass


def _stylesheet_declarations(css: str) -> Counter:
    """Multiset of qualified declarations, including media/supports context."""
    import tinycss2
    counted = Counter()
    def visit(rules, context=()):
        for rule in rules:
            if rule.type == "error":
                raise MigrationRefused(f"Invalid CSS: {rule.message}")
            if rule.type == "at-rule":
                if rule.content is not None:
                    # Do not silently skip nested at-rules we cannot audit.
                    if rule.lower_at_keyword not in {"media", "supports", "container", "layer"}:
                        raise MigrationRefused(f"Unsupported nested @{rule.lower_at_keyword} rule")
                    visit(tinycss2.parse_rule_list(rule.content, skip_comments=True,
                                                   skip_whitespace=True),
                          context + ((rule.lower_at_keyword,
                                      tinycss2.serialize(rule.prelude).strip()),))
                continue
            if rule.type != "qualified-rule":
                continue
            selector = tinycss2.serialize(rule.prelude).strip()
            for decl in tinycss2.parse_declaration_list(rule.content,
                                                          skip_comments=True,skip_whitespace=True):
                if decl.type == "error":
                    raise MigrationRefused(f"Invalid CSS declaration: {decl.message}")
                if decl.type == "declaration":
                    counted[(context, selector, decl.lower_name,
                             tinycss2.serialize(decl.value).strip(), bool(decl.important))] += 1
    visit(tinycss2.parse_stylesheet(css,skip_comments=True,skip_whitespace=True))
    return counted


def validate_candidate(repo: Path, patch_zip: Path) -> dict:
    """Reject fake moves (only CSS edits, no actual law change, added legacy CSS)."""
    if not patch_zip.is_file():
        raise MigrationRefused(f"Candidate ZIP not found: {patch_zip}")
    try:
        with ZipFile(patch_zip) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or not names:
                raise MigrationRefused("ZIP has duplicate paths or is empty")
            for name in names:
                posix = Path(name.replace('\\','/'))
                if (name != posix.as_posix() or posix.is_absolute() or '..' in posix.parts
                    or name.endswith('/') or len(posix.parts) < 3):
                    raise MigrationRefused(f"Unsafe/noncanonical ZIP entry: {name}")
            unexpected = set(names) - ({STYLE} | LAW_PATHS.keys())
            if unexpected:
                raise MigrationRefused(f"Only Workbench CSS and canonical presentation laws are accepted: {sorted(unexpected)}")
            if STYLE not in names:
                raise MigrationRefused("Candidate must remove declarations from Workbench CSS")
            selected_laws = sorted(set(names) & LAW_PATHS.keys())
            if not selected_laws:
                raise MigrationRefused("Candidate must change Theme, Form, or Chrome source code")
            baseline = {}
            proposed = {}
            for name in names:
                source = repo / name
                if not source.is_file():
                    raise MigrationRefused(f"Source not present: {name}")
                baseline[name] = source.read_text(encoding="utf-8")
                proposed[name] = archive.read(name).decode("utf-8")
                if baseline[name] == proposed[name]:
                    raise MigrationRefused(f"Candidate asset unchanged: {name}")
    except (BadZipFile, UnicodeDecodeError) as exc:
        raise MigrationRefused(f"Invalid candidate ZIP or text encoding: {exc}") from exc
    old = _stylesheet_declarations(baseline[STYLE])
    new = _stylesheet_declarations(proposed[STYLE])
    additions = new - old
    if additions:
        raise MigrationRefused("Workbench CSS introduces/rewrites declarations; migration lane permits removal only")
    removed = old - new
    if not removed:
        raise MigrationRefused("Workbench CSS has no removed declarations")
    changed_owners = sorted({LAW_PATHS[name] for name in selected_laws})
    return {
        "baseline": baseline, "proposed": proposed,
        "removed": [{"conditions": list(context), "selector": selector,
                     "property": prop, "value": value, "important": important,
                     "count": count}
                    for (context,selector,prop,value,important),count in removed.items()],
        "laws": selected_laws, "owners": changed_owners,
        "source_hashes": {name:hashlib.sha256(data.encode()).hexdigest()
                           for name,data in baseline.items()},
    }


def candidate_html(base_html: str, candidate: dict) -> str:
    """Replace only real embedded assets; no appended shadow CSS is permitted."""
    result = base_html
    for name, original in candidate["baseline"].items():
        updated = candidate["proposed"][name]
        if name == STYLE:
            # Workbench and SLH embed the source stylesheet as the first part
            # of a regular <style> block. Keep its precise cascade location.
            before = "<style>" + original
            after = "<style>" + updated
        else:
            # Host renders JS as source-bound inline script assets.
            before = "<script>\n" + original + "\n</script>"
            after = "<script>\n" + updated + "\n</script>"
        if result.count(before) != 1:
            raise MigrationRefused(f"Actual application did not embed source asset exactly once: {name}")
        result = result.replace(before, after, 1)
    return result


def removal_only_html(base_html: str, candidate: dict) -> str:
    """Remove legacy CSS in situ while keeping every canonical law unchanged."""
    old = candidate['baseline'][STYLE]
    new = candidate['proposed'][STYLE]
    before = '<style>' + old
    if base_html.count(before) != 1:
        raise MigrationRefused('Baseline lacks uniquely embedded Workbench stylesheet')
    return base_html.replace(before, '<style>' + new, 1)


def _present(page, owner: str) -> bool:
    """Observe a law's real mounted output, not merely the JS global's existence."""
    return bool(page.evaluate("""owner => {
      const root=document.querySelector('#mcel-workbench');
      if(!root || !root.querySelector('[data-mcel-region]'))return false;
      if(owner==='Theme')return !!document.querySelector('style[data-mcel-semantic-color-law]') &&
        !!root.querySelector('[data-mcel-color-role]');
      if(owner==='Form')return !!document.getElementById('mcel-lab-semantic-form-presentation') &&
        !!root.querySelector('[data-mcel-form-presentation-kind]');
      if(owner==='Chrome')return !!document.getElementById('mcel-lab-tng-side-rails-css') &&
        !!root.querySelector('[data-mcel-semantic-chrome]');
      return false;
    }""", owner))


def _readiness(page, app: str) -> None:
    report = page.evaluate("""() => {
      const status=document.getElementById('mcel-workbench-status');
      return {ready:!!document.querySelector('#mcel-workbench [data-mcel-region]'),
        error:status?.dataset?.error==='true',
        status:(status?.textContent||'').slice(0,180)};
    }""")
    if not report['ready'] or report['error'] or 'refused' in report['status'].lower():
        raise RuntimeError(f"{app} did not mount cleanly: {report}")


def _new_page(browser, width: int, html: str, app: str, state: str):
    page = browser.new_page(viewport={"width":width,"height":850},
                            reduced_motion="reduce",device_scale_factor=1,
                            locale="en-US", timezone_id="America/Los_Angeles")
    try:
        page.goto('about:blank')
        page.evaluate("""() => {
          const data=new Map();
          Object.defineProperty(window,'localStorage',{configurable:true,value:{
            getItem:k=>data.get(String(k))??null,
            setItem:(k,v)=>data.set(String(k),String(v)),
            removeItem:k=>data.delete(String(k)),
            clear:()=>data.clear(),
            key:i=>[...data.keys()][i]??null,
            get length(){return data.size;}
          }});
        }""")
        # Activate the existing law only inside this disposable browser scenario.
        # Do not mutate production rollout settings or application source.
        if html.count('</head>') != 1:
            raise MigrationRefused('Application page lacks unique head closing tag')
        html = html.replace('</head>', '</head><script>window.__mcelSemanticColorRollout=true;</script>', 1)
        page.set_content(html, wait_until='domcontentloaded', timeout=30000)
        _state(page,app,state)
        _readiness(page,app)
        page.clock.pause_at(datetime(2026,11,15,12,0,0))
        return page
    except Exception:
        page.close()
        raise


def _exercise(page, phase: str, selector: str) -> bytes:
    """Snapshot one reachable UI phase. No click is completed for pressed state."""
    if phase == 'rest':
        return _capture(page)
    matches = page.locator(selector)
    if not matches.count():
        raise MigrationRefused(f"Interaction selector absent: {selector}")
    target=matches.first
    if not target.is_visible():
        raise MigrationRefused(f"Interaction target hidden: {selector}")
    if phase == 'hover':
        target.hover(timeout=3000)
    elif phase == 'focus':
        page.mouse.move(0,0)
        target.focus(timeout=3000)
    elif phase == 'pressed':
        target.hover(timeout=3000)
        page.mouse.down()
    else:
        raise ValueError(phase)
    return _capture(page)


def migration_status(removal_only_pixels: int, migrated_pixels: int) -> str:
    """Causal verdict; exact match without a visible original dependency is not a move."""
    if not removal_only_pixels:
        return 'REDUNDANT_NO_MIGRATION_NEEDED' if not migrated_pixels else 'LAW_CHANGE_ALTERS_RENDER'
    return 'MIGRATION_PIXEL_MATCH' if not migrated_pixels else 'MIGRATION_PIXEL_DIFFERENCE'


def compare(repo: Path, patch_zip: Path, output: Path, *, apps=DEFAULT_APPS,
            widths=(390,1040,1440), all_views=True,
            interactions=("rest", "hover", "focus", "pressed"),
            selector="button:visible") -> dict:
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    candidate=validate_candidate(repo,patch_zip)
    report={"schema":"mcel.css.migration-pixel-acceptance.v1",
            "status":"UNVERIFIED", "candidate":str(patch_zip),
            "owners":candidate['owners'],"law_files":candidate['laws'],
            "source_hashes":candidate['source_hashes'],
            "removed_declarations":candidate['removed'],
            "apps":list(apps),"widths":list(widths),"all_views":all_views,
            "phases":list(interactions),"selector":selector,"scenarios":[],
            "limitations":"Exact full-page screenshots at captured scroll positions; untested content and interactions are not certified."}
    output.parent.mkdir(parents=True,exist_ok=True)
    def save():
        output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    save()
    with sync_playwright() as playwright:
        browser,source=launch_semantic_browser(playwright)
        report['browser']=source
        try:
            for app in apps:
                if app not in APPS:
                    raise MigrationRefused(f"Unknown Workbench app {app}")
                for state in STATES[app] if all_views else STATES[app][:1]:
                    baseline_html=_app_html(repo,app,state)
                    stripped_html=removal_only_html(baseline_html,candidate)
                    try:
                        edited_html=candidate_html(baseline_html,candidate)
                    except MigrationRefused as exc:
                        for width in widths:
                            for phase in interactions:
                                report['scenarios'].append({
                                    "app":app,"view":state,"width":width,"phase":phase,
                                    "status":"LAW_NOT_EMBEDDED","reason":str(exc)})
                                print(f"[LAW_NOT_EMBEDDED] {app}:{state}:{width}:{phase}",flush=True)
                        save()
                        continue
                    for width in widths:
                        for phase in interactions:
                            item={"app":app,"view":state,"width":width,
                                  "phase":phase,"status":"UNVERIFIED"}
                            opened=[]
                            try:
                                # Two independent original pages must match before
                                # the migration can be called pixel-equivalent.
                                a=_new_page(browser,width,baseline_html,app,state);opened.append(a)
                                b=_new_page(browser,width,baseline_html,app,state);opened.append(b)
                                c=_new_page(browser,width,edited_html,app,state);opened.append(c)
                                d=_new_page(browser,width,stripped_html,app,state);opened.append(d)
                                inactive=[owner for owner in candidate['owners'] if not _present(c,owner)]
                                if inactive:
                                    item.update(status='LAW_INACTIVE',reason=inactive)
                                    continue
                                first=_exercise(a,phase,selector)
                                second=_exercise(b,phase,selector)
                                count,bounds=_pixel_diff(first,second)
                                if count:
                                    item.update(status='BASELINE_UNSTABLE',pixels=count,bounds=bounds)
                                    continue
                                # This counterfactual proves whether legacy CSS
                                # had a visible job before the proposed law change.
                                stripped=_exercise(d,phase,selector)
                                lost_pixels,lost_bounds=_pixel_diff(first,stripped)
                                third=_exercise(c,phase,selector)
                                count,bounds=_pixel_diff(first,third)
                                item.update(pixels=count,bounds=bounds,
                                            removal_only_pixels=lost_pixels,
                                            removal_only_bounds=lost_bounds)
                                item['status']=migration_status(lost_pixels,count)
                            except Exception as exc:
                                item.update(status='ERROR',reason=f'{type(exc).__name__}: {exc}')
                            finally:
                                for page in opened:page.close()
                                report['scenarios'].append(item)
                                print(f"[{item['status']}] {app}:{state}:{width}:{phase}",flush=True)
                                save()
        finally:browser.close()
    totals=Counter(item['status'] for item in report['scenarios'])
    report['totals']=dict(totals)
    # A CSS declaration may be redundant in some views and law-recovered in
    # others. At least one observed law recovery is required for migration.
    acceptable={'MIGRATION_PIXEL_MATCH','REDUNDANT_NO_MIGRATION_NEEDED'}
    report['status']=('PASS_PIXEL_EXACT_SCOPED' if totals and
                      set(totals).issubset(acceptable) and
                      totals.get('MIGRATION_PIXEL_MATCH',0)>0
                      else 'NOT_ACCEPTED')
    save()
    print('SUMMARY',report['status'],dict(totals),'REPORT',str(output),flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,
      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--candidate-zip',required=True,type=Path,
                        help='Changed files, repository-root-relative, in a candidate ZIP')
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,default=Path('runtime/reports/mcel-css-migration.json'))
    parser.add_argument('--apps',default=','.join(DEFAULT_APPS),
                        help='Comma-separated Workbench apps; SLH currently lacks Theme law assets')
    parser.add_argument('--widths',default='390,1040,1440')
    parser.add_argument('--all-views',action='store_true')
    parser.add_argument('--phases',default='rest,hover,focus,pressed')
    parser.add_argument('--selector',default='button:visible')
    args=parser.parse_args(argv)
    apps=[v.strip() for v in args.apps.split(',')]
    widths=[int(v.strip()) for v in args.widths.split(',')]
    phases=[v.strip() for v in args.phases.split(',')]
    if not apps or set(apps)-set(APPS) or not widths or min(widths)<250 or set(phases)-{'rest','hover','focus','pressed'}:
        parser.error('Invalid app, viewport, or interaction phase')
    root=args.root.resolve()
    out=args.output if args.output.is_absolute() else root/args.output
    report=compare(root,args.candidate_zip.resolve(),out,apps=apps,widths=widths,
                   all_views=args.all_views,interactions=phases,selector=args.selector)
    return 0 if report['status']=='PASS_PIXEL_EXACT_SCOPED' else 1


if __name__=='__main__':
    raise SystemExit(main())
