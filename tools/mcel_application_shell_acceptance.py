"""Inspect every canonical MCEL application through the running HTTPS gateway.

No production source/configuration mutation, no browser profile modification,
no app-control clicks, and no document writes are requested. A fresh proof-bound
session is issued and revoked; normal app initialization may perform its usual
background operations. Reports contain status codes/paths, not credentials or
response bodies. This is mount/transport acceptance, not functional acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = Path('tools/fixtures/mcel_semantic_applications_shell/registry.json')
# Generated semantic hosts do not render their legacy DOM application IDs.
# Require the operational runtime's mount witness AND a visible rendered child.
WORKBENCH = '#mcel-workbench[data-mcel-semantic-operational] > *'
ROOTS = {'webgl': '#webgl-demo', 'desktop': '#mcel-shell-overview',
         'calculator': '#mcel-expanded-root[data-mcel-semantic-operational] > *',
         **dict.fromkeys(('file-explorer', 'task-manager', 'code-editor', 'worker', 'slh'), WORKBENCH)}
MOUNT_ERRORS = {'calculator': '#mcel-expanded-diagnostic[data-error="true"]',
                **dict.fromkeys(('file-explorer', 'task-manager', 'code-editor', 'worker', 'slh'),
                                '#mcel-workbench-status[data-error="true"]')}
ERRORS = {'BACKEND_RESPONSE_TOO_LARGE', 'MC_BACKEND_UNAVAILABLE', 'AUTH_REQUIRED',
          'INVALID_HOST', 'MC_BACKEND_NOT_CONFIGURED', 'INVALID_APPLICATION_ORIGIN'}


def inventory(root):
    from main_computer.mcel_applications_registry import read_registry
    registry = read_registry(root, validate_presentation=False)
    return [{**app, 'selector': ROOTS.get(app['id'], '#'+app['id']+'-app'),
             'mount_error_selector': MOUNT_ERRORS.get(app['id'])}
            for app in [*registry['apps'], *registry.get('runtimeApps', [])]
            if app in registry['apps'] or app.get('launcher') is True]


def classify(parent, child, visible, nested, error=None):
    if parent != 200:
        return 'PARENT_HTTP_'+str(parent) if parent is not None else 'PARENT_NAVIGATION_FAILED'
    if child is None:
        return 'CHILD_NAVIGATION_NOT_OBSERVED'
    if child['status'] != 200:
        return child.get('error') or 'CHILD_HTTP_'+str(child['status'])
    if child.get('frame') == 'DENY' or "frame-ancestors 'none'" in child.get('csp',''):
        return 'CHILD_FRAME_DENIED'
    if nested:
        return 'CHILD_RECURSIVE_SHELL'
    if error == 'CHILD_SEMANTIC_MOUNT_REFUSED':
        return error
    if not visible:
        return error or 'CHILD_ROOT_NOT_VISIBLE'
    return 'PASS'


def gateway_error(payload):
    # Never persist arbitrary response bodies, URLs, or credentials.
    code = payload.get('error') if isinstance(payload, dict) else None
    return code if isinstance(code, str) and code in ERRORS else None


def diagnose_child_502(context, origin, app, timeout_ms):
    # Chromium can discard the response body when a 502 error document is
    # refused by X-Frame-Options. Repeat the exact canonical child GET via the
    # same authenticated browser context, without following redirects.
    response = None
    try:
        response = context.request.get(origin + app['route'] + '?mcel-shell-child=1',
            headers={'Accept': 'application/json,text/html'},
            max_redirects=0, timeout=timeout_ms)
        code = gateway_error(response.json()) if response.status == 502 else None
        return {'diagnostic_http': response.status, 'diagnostic_error': code}
    except Exception as exc:
        return {'diagnostic_failure': type(exc).__name__}
    finally:
        if response is not None:
            response.dispose()


def inspect_app(context, origin, app, timeout_ms):
    page = context.new_page()
    events, failed, child = [], [], None
    parent_status, visible, nested, error = None, False, False, None
    def local_path(url):
        if not url.startswith(origin+'/'):
            return None
        return urlsplit(url).path
    def on_response(response):
        path = local_path(response.url)
        if path is None:
            return
        try:
            request = response.request
            if not request.is_navigation_request() and response.status < 400:
                return
            headers = response.headers
            row = {'path':path, 'status':response.status, 'type':request.resource_type,
                   'frame':headers.get('x-frame-options','').upper(),
                   'csp':headers.get('content-security-policy',''),
                   'bytes':headers.get('content-length','')}
            # Distinguish the shell's iframe from its parent and from nested third-party frames.
            if request.is_navigation_request():
                frame = request.frame
                row['shell_child'] = frame.parent_frame == page.main_frame if frame != page.main_frame else False
                row['top'] = frame == page.main_frame
            if len(events) < 160:
                events.append(row)
            if row.get('shell_child') and response.status >= 400 and 'json' in headers.get('content-type',''):
                try:
                    code = gateway_error(response.json())
                    if code: row['error'] = code
                except Exception:
                    pass
        except Exception:
            pass
    def on_failure(request):
        path = local_path(request.url)
        if path is not None and len(failed)<80:
            failed.append({'path':path,'type':request.resource_type,'failure':request.failure})
    def last_child():
        children = [r for r in events if r.get('shell_child')]
        if children:
            return children[-1]
        matches = [r for r in events if r['path']==app['route'] and r['type']=='document' and not r.get('top',False)]
        return matches[-1] if matches else None
    page.on('response', on_response)
    page.on('requestfailed', on_failure)
    try:
        response = page.goto(origin+app['route'],wait_until='domcontentloaded',timeout=timeout_ms)
        parent_status = response.status if response else None
        if parent_status == 200 and urlsplit(page.url).path==app['route']:
            if app['mount']=='overview':
                page.locator(app['selector']).wait_for(state='visible',timeout=timeout_ms)
                visible = True
            else:
                frame = page.frame_locator('#mcel-shell-frame')
                deadline=time.monotonic()+timeout_ms/1000
                while time.monotonic()<deadline:
                    observed=last_child()
                    if observed and (observed['status']!=200 or observed.get('frame')=='DENY' or "frame-ancestors 'none'" in observed.get('csp','')):
                        break
                    try:
                        visible=frame.locator(app['selector']).first.is_visible()
                        mount_error = app.get('mount_error_selector')
                        if mount_error and frame.locator(mount_error).count():
                            error='CHILD_SEMANTIC_MOUNT_REFUSED'
                            break
                        nested=frame.locator('#mcel-applications-shell').count()>0
                    except Exception:
                        pass
                    if visible or nested:
                        break
                    page.wait_for_timeout(250)
                if not visible and error is None:
                    error='CHILD_ROOT_NOT_VISIBLE'
        child=last_child() if app['mount']!='overview' else None
        if child and child['status']==502 and not child.get('error'):
            child.update(diagnose_child_502(context, origin, app, timeout_ms))
            if child.get('diagnostic_http') == 502 and child.get('diagnostic_error'):
                child['error'] = child['diagnostic_error']
        result = ('PASS' if visible and parent_status==200 else 'DESKTOP_NOT_VISIBLE') if app['mount']=='overview' else classify(parent_status,child,visible,nested,error)
    except Exception as exc:
        result = 'PARENT_NAVIGATION_FAILED' if parent_status is None else 'SHELL_INSPECTION_'+type(exc).__name__
    finally:
        page.close()
    return {'app':app['id'],'mount':app['mount'],'result':result,'parent_http':parent_status,
            'child':child,'root_visible':visible,'nested_shell':nested,
            'responses':events,'failed_requests':failed}


def run(root, lane, timeout_ms, apps):
    from main_computer import onlyoffice_deployment as d
    from main_computer.auth_hostnames import HOSTS
    from main_computer.auth_bootstrap import BootstrapAuthority, proof_file_path
    from tools.mcel_onlyoffice_cutover import auth_cookie
    from playwright.sync_api import sync_playwright
    state=d.state_path(); policy=d.read_policy(state)
    if not policy or policy['mode']!='hardened' or lane not in policy['roots']:
        raise RuntimeError('HARDENED_REGISTERED_LANE_REQUIRED')
    if not d.same_path(policy['roots'][lane], root):
        raise RuntimeError('RUN_FROM_SELECTED_LANE_ROOT')
    origin='https://'+HOSTS[lane]+':8875'
    cert=state/'tls-cert-localhost.pem'
    rows=[]
    out=root/'runtime/reports/mcel-application-shell-acceptance'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True,exist_ok=False)
    cookie=auth_cookie(SimpleNamespace(server_port=8875),proof_file_path(state),cert,host=HOSTS[lane])
    name,token=cookie.split('=',1)
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                context=browser.new_context(ignore_https_errors=False)
                context.add_cookies([{'name':name,'value':token,'url':origin,'secure':True,'httpOnly':True,'sameSite':'Strict'}])
                for app in apps:
                    row=inspect_app(context,origin,app,timeout_ms)
                    rows.append(row)
                    print(f"MCEL_SHELL {app['id']} {app['mount']} {row['result']} parent={row['parent_http']} child={row['child']['status'] if row['child'] else None}",flush=True)
                    (out/'results.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
            finally:
                browser.close()
    finally:
        BootstrapAuthority(state).sessions.revoke(token)
    counts=dict(Counter(row['result'] for row in rows))
    verdict='PASS_SHELL_MOUNTS_ONLY' if len(rows)==len(apps) and counts.get('PASS',0)==len(apps) else 'BLOCKED'
    report={'verdict':verdict,'lane':lane,'counts':counts,'tested':len(rows),'expected':len(apps),
            'production_routes_changed':False,'functional_certification':False,'report_dir':str(out)}
    (out/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('MCEL_SHELL SUMMARY '+json.dumps(report),flush=True)
    return 0 if verdict.startswith('PASS') else 1


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lane',choices=('dev','unleashed','debug','safe'),default='dev')
    p.add_argument('--app',action='append',help='Optional canonical app id; repeat to select several')
    p.add_argument('--timeout',type=int,default=60,help='Per-navigation and mount timeout in seconds')
    p.add_argument('--list',action='store_true')
    args=p.parse_args(); apps=inventory(ROOT)
    if args.app:
        ids=set(args.app)
        if ids-{a['id'] for a in apps}: p.error('unknown application id')
        apps=[a for a in apps if a['id'] in ids]
    if args.list:
        for a in apps: print(a['id'],a['mount'],a['route'],a['selector'])
        return 0
    if not 5<=args.timeout<=300: p.error('timeout must be 5–300 seconds')
    return run(ROOT,args.lane,args.timeout*1000,apps)

if __name__=='__main__':
    raise SystemExit(main())
