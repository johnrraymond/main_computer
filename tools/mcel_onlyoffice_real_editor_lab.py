"""Disposable *real editor* ONLYOFFICE / MC-session acceptance experiment.

Runs a REAL ONLYOFFICE Document Server, an isolated copy of MC's current
BootstrapAuthority/SessionStore and real callback source handler, an ephemeral
Nginx TLS reverse proxy, and an isolated Playwright-managed browser. Saves and
reopens a real XLSX. A credentialless Bob container probes internal interfaces.

The successful result establishes the *disposable end-to-end editor path*, not
that current production routes have been secured. This lab uses a temporary
self-signed certificate, pins its SPKI in the disposable Chromium process,
and separately verifies SAN/SNI through Python's TLS verifier. It does not
certify Windows CurrentUser trust, real second-user separation, or mDNS denial.

Run on the Windows Docker host from the MC repo:
  python -m tools.mcel_onlyoffice_real_editor_lab run

No MC application process, production browser profile, production Docker
volume, production proof file, Windows certificate store, or standard port is
modified. A random Compose project is always torn down, even on failure.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
HOST_DEV = 'dev.main-computer.local'
HOST_DOCS = 'onlyoffice.main-computer.local'
SUFFIX = '.main-computer.local'
SOURCE_FILES = [
    ROOT/'main_computer'/'auth_bootstrap.py',
    ROOT/'main_computer'/'viewport_routes_onlyoffice.py',
    ROOT/'tools'/'mcel_onlyoffice_real_editor_fixture.py',
    ROOT/'tools'/'mcel_onlyoffice_real_editor_bob.py',
]


def check(name, condition, detail=''):
    status='PASS' if condition else 'FAIL'
    print(f'MC_ONLYOFFICE_REAL {name} {status}' + (f' detail={detail}' if detail else ''),flush=True)
    if not condition:raise AssertionError(name+(' '+detail if detail else ''))


def run_command(args, timeout=60, capture=False):
    result=subprocess.run(args,check=False,timeout=timeout,text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)
    if result.returncode:
        raise RuntimeError(f'command failed exit={result.returncode} operation={args[:3]}'+
                           (f' stderr={result.stderr[:180]}' if capture and result.stderr else ''))
    return result


def create_workbook(path, old_marker):
    """Create a minimal but valid Excel XLSX without relying on openpyxl."""
    sheet='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <dimension ref="A1:B2"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>
      <sheetFormatPr defaultRowHeight="15"/><sheetData>
      <row r="1"><c r="A1" t="inlineStr"><is><t>'''+old_marker+'''</t></is></c><c r="B1"><v>1</v></c></row>
      <row r="2"><c r="A2"><v>2</v></c></row></sheetData>
    </worksheet>'''
    files={
      '[Content_Types].xml':'''<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
 <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
 <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>''',
      '_rels/.rels':'''<?xml version="1.0" encoding="utf-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''',
      'xl/workbook.xml':'''<?xml version="1.0" encoding="utf-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>''',
      'xl/_rels/workbook.xml.rels':'''<?xml version="1.0" encoding="utf-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>''',
      'xl/styles.xml':'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>''',
      'xl/worksheets/sheet1.xml':sheet,
    }
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as output:
        for name, data in files.items():output.writestr(name,data)


def get_cell(path, reference='A1'):
    """Read common XLSX cell encodings (inline or shared strings)."""
    namespace={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as file:
        sheet=ET.fromstring(file.read('xl/worksheets/sheet1.xml'))
        cell=sheet.find(f'.//x:c[@r="{reference}"]',namespace)
        if cell is None:return None
        kind=cell.get('t')
        if kind=='inlineStr':return ''.join(v.text or '' for v in cell.findall('.//x:t',namespace))
        value=cell.find('x:v',namespace)
        if value is None:return None
        if kind=='s':
            strings=ET.fromstring(file.read('xl/sharedStrings.xml'))
            selected=strings.findall('x:si',namespace)[int(value.text)]
            return ''.join(v.text or '' for v in selected.findall('.//x:t',namespace))
        return value.text


def certificate(work):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,HOST_DEV)])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key())
      .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=2))
      .not_valid_after(now+timedelta(days=1))
      .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST_DEV),x509.DNSName(HOST_DOCS)]),critical=False)
      .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
      .sign(private,hashes.SHA256()))
    cert_path=work/'cert.pem';key_path=work/'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(private.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    if os.name!='nt':os.chmod(key_path,0o600)
    return cert_path,key_path


def nginx_config():
    return r'''
map $http_upgrade $connection_upgrade { default upgrade; '' close; }
# Log ONLY whether the actual TLS-terminated docs request carried the MC
# session cookie. NEVER log the cookie value, document URLs or credentials.
# This server-side audit avoids Playwright request.all_headers() deadlocks.
map $http_cookie $mc_docs_has_session_cookie {
 default 0;
 ~__Host-MC-Session= 1;
}
log_format mc_docs_cookie_audit '$mc_docs_has_session_cookie';
server { listen 443 ssl default_server; server_name _;
 ssl_certificate /etc/lab/cert.pem; ssl_certificate_key /etc/lab/key.pem;
 return 421;
}
server { listen 443 ssl; server_name dev.main-computer.local;
 ssl_certificate /etc/lab/cert.pem; ssl_certificate_key /etc/lab/key.pem;
 location / { proxy_pass http://fixture:8000; proxy_http_version 1.1;
   proxy_set_header Host $http_host; proxy_set_header X-Forwarded-Proto https;
   proxy_set_header X-Forwarded-Host $http_host; proxy_set_header Connection close;
   proxy_read_timeout 120s; proxy_send_timeout 120s; }
}
server { listen 443 ssl; server_name onlyoffice.main-computer.local;
 ssl_certificate /etc/lab/cert.pem; ssl_certificate_key /etc/lab/key.pem;
 # Scoped launch pages live on the editor origin, NOT the privileged MC origin.
 # This access log stores one bit per request, not Cookie or URL contents.
 access_log /var/log/nginx/mc-docs-cookie-audit.log mc_docs_cookie_audit;
 location = /mc-editor { proxy_pass http://fixture:8000;
   proxy_set_header Host $http_host; proxy_set_header X-Forwarded-Proto https;
   proxy_set_header X-Forwarded-Host $http_host; }
 location = /mc-config { proxy_pass http://fixture:8000;
   proxy_set_header Host $http_host; proxy_set_header X-Forwarded-Proto https;
   proxy_set_header X-Forwarded-Host $http_host; }
 # Only the readiness endpoint gets a bounded outage timeout. Keep editor
 # WebSocket/long-poll request budgets unchanged; the fault harness must
 # observe a real upstream error instead of timing out its own HTTP client.
 location = /healthcheck { proxy_pass http://onlyoffice:80/healthcheck;
   proxy_set_header Host $http_host;
   proxy_set_header X-Forwarded-Proto https;
   proxy_connect_timeout 3s; proxy_read_timeout 3s; proxy_send_timeout 3s; }
 location / { proxy_pass http://onlyoffice:80; proxy_http_version 1.1;
   proxy_set_header Host $http_host;
   proxy_set_header X-Forwarded-Proto https;
   proxy_set_header X-Forwarded-Host $http_host;
   proxy_set_header X-Forwarded-Port $server_port;
   proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   proxy_set_header Upgrade $http_upgrade;
   proxy_set_header Connection $connection_upgrade;
   proxy_read_timeout 600s; proxy_send_timeout 600s; proxy_buffering off; }
}
'''


def available_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def manifest(work,port,auth_secret,jwt_secret,tag,shadow_revision=False,continuous_saves=False,durable_restart=False,final_close_probe=False):
    bind=lambda source,target,ro=True:{'type':'bind','source':str(source.resolve()),'target':target,'read_only':ro}
    # Credentials are disposable and never taken from MC's production state.
    onlyoffice={'image':'onlyoffice/documentserver:'+tag, 'restart':'no',
        'environment':{'JWT_ENABLED':'true','JWT_SECRET':jwt_secret,
                       'ALLOW_PRIVATE_IP_ADDRESS':'true','ALLOW_META_IP_ADDRESS':'false'},
        'shm_size':'512m','networks':['sandbox']}
    fixture={'image':'python:3.12-slim','restart':'no',
        'command':['python','-u','/lab/fixture.py'], 'working_dir':'/lab',
        'environment':{'PYTHONPATH':'/lab','LAB_AUTH_KEY_B64':auth_secret,
                       'LAB_JWT_SECRET':jwt_secret,'LAB_BROWSER_PORT':str(port),
                       'LAB_SHADOW_REVISION_FENCE':'1' if shadow_revision else '0',
                        'LAB_SHADOW_CONTINUOUS_SAVE':'1' if continuous_saves else '0',
                        'LAB_DURABLE_SAVE_JOURNAL':'1' if durable_restart else '0',
                        'LAB_FINAL_CLOSE_PROBE':'1' if final_close_probe else '0'},
        'volumes':[bind(ROOT/'main_computer'/'auth_bootstrap.py','/lab/main_computer/auth_bootstrap.py'),
                   bind(ROOT/'main_computer'/'viewport_routes_onlyoffice.py','/lab/viewport_routes_onlyoffice.py'),
                   bind(ROOT/'tools'/'mcel_onlyoffice_real_editor_fixture.py','/lab/fixture.py'),
                   bind(work/'documents','/lab/documents',False)],
        'networks':['sandbox'],'security_opt':['no-new-privileges:true']}
    if shadow_revision:
        # Test-only storage candidate. Never mount production user state.
        fixture['volumes'].append(bind(ROOT/'tools'/'mcel_onlyoffice_shadow_revision.py',
                                      '/lab/mcel_onlyoffice_shadow_revision.py'))
    if durable_restart:
        fixture['volumes'].append(bind(ROOT/'tools'/'mcel_onlyoffice_durable_save_journal.py',
                                      '/lab/mcel_onlyoffice_durable_save_journal.py'))
    # A host-published port cannot be served by a container attached ONLY to
    # an internal bridge network (Docker Engine 29.x accepts the requested
    # HostConfig binding but does not install the actual host-port mapping).
    # Put only the TLS reverse proxy on a separate, disposable ingress bridge;
    # keep Document Server, the MC fixture and Bob exclusively on the private
    # internal network. No additional ports or host interfaces are exposed.
    nginx={'image':'nginx:1.27-alpine','restart':'no','networks':['ingress','sandbox'],
        'ports':[{'target':443,'published':port,'host_ip':'127.0.0.1','protocol':'tcp'}],
        'volumes':[bind(work/'nginx.conf','/etc/nginx/conf.d/default.conf'),
                   bind(work/'cert.pem','/etc/lab/cert.pem'),bind(work/'key.pem','/etc/lab/key.pem')],
        'security_opt':['no-new-privileges:true']}
    bob={'image':'python:3.12-slim','restart':'no','networks':['sandbox'],
         'user':'65534:65534','read_only':True,'cap_drop':['ALL'],
         'security_opt':['no-new-privileges:true'], 'environment':{'LAB_BROWSER_PORT':str(port)},
         'volumes':[bind(ROOT/'tools'/'mcel_onlyoffice_real_editor_bob.py','/lab/bob.py')],
         'command':['python','-u','/lab/bob.py']}
    return {'services':{'onlyoffice':onlyoffice,'fixture':fixture,'proxy':nginx,'bob':bob},
            'networks':{'sandbox':{'internal':True}, 'ingress':{}}}


def https_request(port,hostname,path,cert_file,method='GET',payload=None,verify_hostname=True,headers=None):
    # Verify the generated certificate's actual SNI/SAN rather than disabling
    # checks in this independent transport test.
    ctx=ssl.create_default_context(cafile=str(cert_file));ctx.check_hostname=verify_hostname
    sock=socket.create_connection(('127.0.0.1',port),timeout=10)
    try:
        tls=ctx.wrap_socket(sock,server_hostname=hostname)
        try:
            data=payload or b''
            # Playwright's APIRequestContext does NOT use Chromium's
            # --host-resolver-rules. This socket intentionally connects to the
            # disposable 127.0.0.1 port, while SNI and certificate hostname
            # verification still use the approved .local hostname.
            extra=[]
            for name,value in (headers or {}).items():
                if (not re.fullmatch(r'[A-Za-z0-9-]+',name) or
                        name.lower() in ('host','connection','content-length') or
                        '\r' in str(value) or '\n' in str(value)):
                    raise ValueError('unsafe test HTTP header')
                extra.append(f'{name}: {value}\r\n')
            request=(f'{method} {path} HTTP/1.1\r\nHost: {hostname}:{port}\r\nConnection: close\r\n'
                    + ''.join(extra) + f'Content-Length: {len(data)}\r\n\r\n').encode()+data
            tls.sendall(request)
            head=b''
            while b'\r\n\r\n' not in head and len(head)<32768:
                buf=tls.recv(4096)
                if not buf:break
                head+=buf
            return int(head.split(b' ',2)[1])
        finally: tls.close()
    finally: sock.close()


def wait_for_https(port,cert_file,seconds=210):
    last='not started'
    end=time.monotonic()+seconds
    attempt=0
    while time.monotonic()<end:
        attempt+=1
        try:
            gate=https_request(port,HOST_DEV,'/health',cert_file)
            docs=https_request(port,HOST_DOCS,'/healthcheck',cert_file)
            if gate==200 and docs==200:
                check('https-gateway-and-real-documentserver-ready',True,f'attempts={attempt}')
                return
            last=f'gateway={gate} documentserver={docs}'
        except (OSError,TimeoutError,ValueError,ssl.SSLError) as e:
            last=f'{type(e).__name__}: {str(e)[:100]}'
        if attempt in (1,10,30,60):
            print(f'MC_ONLYOFFICE_REAL readiness-retry attempt={attempt} last={last}',flush=True)
        time.sleep(2)
    raise RuntimeError('ONLYOFFICE_REAL_READINESS_TIMEOUT '+last)


def choose_browser(playwright,cert_file,exe=None):
    mapping=(f'MAP {HOST_DEV} 127.0.0.1, '
             f'MAP {HOST_DOCS} 127.0.0.1, '
             f'MAP *{SUFFIX} ^NOTFOUND, EXCLUDE localhost')
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    cert=x509.load_pem_x509_certificate(Path(cert_file).read_bytes())
    spki=cert.public_key().public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    pin=base64.b64encode(hashlib.sha256(spki).digest()).decode('ascii')
    # ONLY the ephemeral certificate's public key is allowed by Chromium. No
    # system trust change and no blanket ignore_https_errors bypass.
    kwargs={'headless':True, 'args':['--host-resolver-rules='+mapping,
                                    '--ignore-certificate-errors-spki-list='+pin,
                                    '--no-proxy-server']}
    if exe:
        kwargs['executable_path']=exe
        return playwright.chromium.launch(**kwargs)
    for channel in ('msedge','chrome',None):
        try:
            if channel: return playwright.chromium.launch(channel=channel,**kwargs)
            return playwright.chromium.launch(**kwargs)
        except Exception:
            pass
    raise RuntimeError('PLAYWRIGHT_CHROMIUM_NOT_INSTALLED; use --browser-executable or install Edge')


def read_proxy_document_cookie_audit(compose):
    """Read the actual docs-origin cookie audit without querying Playwright.

    Nginx writes ONE BIT per completed HTTPS request, with no credentials,
    request paths, or document identifiers. The subprocess is time-bounded.
    Missing or invalid server-side evidence is an error, never a PASS.
    """
    result=run_command([*compose,'exec','-T','proxy','cat',
                        '/var/log/nginx/mc-docs-cookie-audit.log'],
                       timeout=15,capture=True)
    entries=result.stdout.splitlines()
    if not entries or any(entry not in ('0','1') for entry in entries):
        raise RuntimeError('DOC_ORIGIN_COOKIE_AUDIT_UNVERIFIED missing-or-invalid-proxy-log')
    return entries


def audit_document_request_cookies(entries,initial_count):
    """Analyze only requests made after login/edit monitoring began.

    The initial count excludes Document Server readiness probes. The audit
    fails closed if the append-only proxy log is missing, malformed, truncated,
    or contains no actual editor-origin requests. One ``1`` means the MC
    session cookie crossed to the ONLYOFFICE origin and acceptance must fail.
    """
    if (not isinstance(initial_count,int) or initial_count<0 or
            not isinstance(entries,(list,tuple)) or
            len(entries)<initial_count or
            any(entry not in ('0','1') for entry in entries)):
        raise RuntimeError('DOC_ORIGIN_COOKIE_AUDIT_UNVERIFIED invalid-or-truncated-log')
    relevant=entries[initial_count:]
    if not relevant:
        raise RuntimeError('DOC_ORIGIN_COOKIE_AUDIT_UNVERIFIED no-editor-origin-requests')
    return len(relevant),('1' in relevant)


def browser_round_trip(port,proof_file,workbook,new_marker,cert_file,compose,
                       baseline_audit_count,exe=None,timeout=180,on_persisted=None,
                       require_shadow=False,continuous_saves=False,durable_restart=False,
                       final_close_probe=False):
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    except ImportError as e:
        raise RuntimeError('PLAYWRIGHT_NOT_INSTALLED: pip install playwright') from e
    base=f'https://{HOST_DEV}:{port}'
    with sync_playwright() as driver:
        browser=choose_browser(driver,cert_file,exe)
        try:
            context=browser.new_context(ignore_https_errors=False,accept_downloads=False)
            # ONLYOFFICE-origin Cookie headers are audited at the disposable
            # HTTPS proxy. No Playwright request event handlers or historical
            # request.all_headers() calls are needed (they can block Windows).
            page=context.new_page()
            page.set_default_timeout(30000)
            # A new browser has no session and must not enter the editor.
            page.goto(base+'/graphical',wait_until='domcontentloaded',timeout=30000)
            check('browser-first-visit-demands-login',page.url.startswith(base+'/auth/login'))
            page.locator('#proof').set_input_files(str(proof_file))
            page.wait_for_url(base+'/graphical',timeout=45000)
            cookies=context.cookies([base])
            auth_cookies=[c for c in cookies if c['name']=='__Host-MC-Session']
            check('one-real-auth-authority-session-established',len(auth_cookies)==1)
            check('session-cookie-secure-httponly-host-restricted',
                  len(auth_cookies)==1 and auth_cookies[0]['secure'] and auth_cookies[0]['httpOnly']
                  and auth_cookies[0]['sameSite']=='Strict'
                  and auth_cookies[0]['domain']==HOST_DEV)
            # Deliberately send a hostile Origin with an actual valid MC
            # session cookie. This is stricter than ordinary browser CSRF
            # (SameSite=Strict would normally withhold that cookie). Use the
            # pinned-certificate HTTPS socket, NOT Playwright's independent
            # APIRequestContext, which does not inherit browser DNS rules.
            hostile_status=https_request(port,HOST_DEV,'/api/onlyoffice/forcesave',cert_file,
                method='POST',payload=json.dumps({'action':'forcesave'}).encode(),
                headers={'Origin':f'https://{HOST_DOCS}:{port}',
                         'Content-Type':'application/json',
                         'Cookie':f"__Host-MC-Session={auth_cookies[0]['value']}"})
            check('editor-origin-cannot-csrf-mc-control-with-cookie',hostile_status in (401,403),
                  f'HTTP={hostile_status}')

            check('untrusted-onlyoffice-js-not-in-mc-origin',
                  page.evaluate('typeof window.DocsAPI') == 'undefined' and
                  page.locator('iframe#mc-docs').count()==1 and
                  page.locator('script[src*="onlyoffice.main-computer.local"]').count()==0)
            page.frame_locator('#mc-docs').locator('body').wait_for(timeout=45000)
            docs_frame=next((f for f in page.frames if '/mc-editor' in f.url and
                             f.url.startswith(f'https://{HOST_DOCS}:')),None)
            check('isolated-docs-host-origin-created',docs_frame is not None)
            docs_frame.wait_for_function('window.MC_DOC_READY === true',timeout=120000)
            check('real-onlyoffice-editor-document-ready',True,
                  'events='+str(docs_frame.evaluate('window.MC_EDITOR_EVENTS'))[:180])
            original_key=docs_frame.evaluate('window.MC_DOCUMENT_KEY')
            # Find the actual editor iframe, not a synthetic text input.
            editor_frames=[f for f in page.frames if 'spreadsheeteditor' in f.url and f!=page.main_frame]
            check('real-spreadsheet-editor-iframe',bool(editor_frames),f'frames={len(editor_frames)}')
            frame=editor_frames[-1]
            # Focus the real worksheet canvas and edit A1. Selection and
            # keypresses must reach Document Server's editor UI.
            canvas=None
            for selector in ('#editor_sdk', '.sdk-canvas', 'canvas'):
                loc=frame.locator(selector)
                try:
                    if loc.count() and loc.first.is_visible(timeout=2000):
                        canvas=loc.first; break
                except Exception:pass
            check('real-editor-canvas-observed',canvas is not None)
            bounds=canvas.bounding_box()
            check('editor-canvas-has-geometry',bounds is not None and bounds['width']>60 and bounds['height']>60)
            canvas.click(position={'x':min(140, max(10,bounds['width']//3)),
                                   'y':min(120,max(10,bounds['height']//3))},timeout=15000)
            page.keyboard.press('Control+Home')
            page.keyboard.press('F2')
            page.keyboard.press('Control+A')
            page.keyboard.type(new_marker,delay=25)
            page.keyboard.press('Enter')
            time.sleep(3)
            # ForceSave is ONLYOFFICE's real editing command; its callback
            # must contain the edited bytes. A synthetic callback is NOT enough.
            force_response=None
            for attempt in range(8):
                # A running editor must preserve its original document key.
                # The test-only command request supplies that key explicitly;
                # it is validated against server-issued grants on the backend.
                payload=({'action':'forcesave','key':original_key} if continuous_saves
                         else {'action':'forcesave'})
                response=page.evaluate('''async(payload)=>{const r=await fetch('/api/onlyoffice/forcesave',{
                  method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify(payload)});return {status:r.status,body:await r.json()}}''',payload)
                force_response=response
                if response['status']==200 and response['body'].get('error')==0:break
                time.sleep(3)
            check('real-documentserver-forcesave-command-accepted',force_response is not None and
                  force_response['status']==200 and force_response['body'].get('error')==0,
                  'response='+str(force_response)[:180])
            found=False;observed=None
            until=time.monotonic()+timeout
            while time.monotonic()<until:
                try: observed=get_cell(workbook,'A1')
                except (OSError,ValueError,KeyError,zipfile.BadZipFile):observed=None
                if observed==new_marker:found=True;break
                time.sleep(2)
            check('genuine-editor-callback-saved-changed-cell',found,
                  'A1='+repr(observed)[:90])
            first_userdata=None
            if durable_restart:
                first_userdata=force_response['body'].get('_lab_userdata')
                check('durable-first-save-has-issued-command',
                      isinstance(first_userdata,str) and first_userdata.count('.')==1)
                before_restart=page.evaluate('''async()=>{const r=await fetch('/api/onlyoffice/metrics',
                    {credentials:'same-origin'});return {status:r.status,body:await r.json()}}''')
                prior_boot=before_restart['body'].get('server_boot_id')
                prior_journal=before_restart['body'].get('durable_journal') or {}
                check('durable-first-save-recorded-on-disk',
                      before_restart['status']==200 and prior_journal.get('accepted')==1 and
                      prior_journal.get('next')==1 and prior_journal.get('key')==original_key)
                # Restart only the disposable backend. The ONLYOFFICE editor,
                # its Document Server container and Chromium session stay open.
                print('MC_ONLYOFFICE_REAL durable-disposable-backend-restart START',flush=True)
                run_command([*compose,'restart','fixture'],timeout=120)
                wait_for_https(port,cert_file,seconds=160)
                check('durable-editor-remains-open-during-backend-restart',
                      docs_frame.evaluate('window.MC_DOCUMENT_KEY')==original_key and
                      docs_frame.evaluate('window.MC_DOC_READY') is True)
                after_restart=page.evaluate('''async()=>{const r=await fetch('/api/onlyoffice/metrics',
                    {credentials:'same-origin'});return {status:r.status,body:await r.json()}}''')
                journal=after_restart['body'].get('durable_journal') or {}
                check('durable-backend-restarted-with-different-process',
                      after_restart['status']==200 and prior_boot and
                      after_restart['body'].get('server_boot_id')!=prior_boot)
                check('durable-session-still-authenticated-after-restart',
                      after_restart['status']==200)
                check('durable-first-save-state-recovered-across-restart',
                      journal.get('accepted')==1 and journal.get('next')==1 and
                      journal.get('key')==original_key)
            if continuous_saves:
                # Same live iframe, same document key, second genuine editor
                # change and a second CommandService status=6 callback. No
                # reload or fresh editor grant is permitted between saves.
                check('continuous-document-key-stays-stable',
                      docs_frame.evaluate('window.MC_DOCUMENT_KEY')==original_key)
                second_marker=new_marker+'_SECOND'
                canvas.click(position={'x':min(140,max(10,bounds['width']//3)),
                                       'y':min(120,max(10,bounds['height']//3))},timeout=15000)
                page.keyboard.press('Control+Home')
                page.keyboard.press('F2')
                page.keyboard.press('Control+A')
                page.keyboard.type(second_marker,delay=25)
                page.keyboard.press('Enter')
                time.sleep(3)
                second_response=page.evaluate('''async(key)=>{const r=await fetch('/api/onlyoffice/forcesave',{
                  method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({action:'forcesave',key})});
                  return {status:r.status,body:await r.json()}}''',original_key)
                check('continuous-second-forcesave-command-accepted',
                      second_response['status']==200 and second_response['body'].get('error')==0,
                      'response='+str(second_response)[:180])
                deadline=time.monotonic()+90
                observed2=None
                while time.monotonic()<deadline:
                    try:observed2=get_cell(workbook,'A1')
                    except (OSError,ValueError,KeyError,zipfile.BadZipFile):observed2=None
                    if observed2==second_marker:break
                    time.sleep(2)
                if observed2!=second_marker:
                    diag=page.evaluate('''async()=>{const r=await fetch('/api/onlyoffice/metrics',
                       {credentials:'same-origin'});return r.json()}''')
                    print('MC_ONLYOFFICE_REAL CONTINUOUS_SAVE_DIAGNOSTIC '+json.dumps({
                        'observed_cell':observed2,'save_seqs':diag.get('continuous_seqs'),
                        'continuous_commits':diag.get('continuous_commits'),
                        'continuous_denials':diag.get('continuous_denials'),
                        'document_key_still_original':docs_frame.evaluate('window.MC_DOCUMENT_KEY')==original_key
                    },sort_keys=True),flush=True)
                check('continuous-second-genuine-editor-save-persisted',observed2==second_marker,
                      'A1='+repr(observed2)[:90])
                new_marker=second_marker
                check('continuous-document-key-remains-stable-after-second-save',
                      docs_frame.evaluate('window.MC_DOCUMENT_KEY')==original_key)
                state=page.evaluate('''async()=>{const r=await fetch('/api/onlyoffice/metrics',
                   {credentials:'same-origin'});return r.json()}''')
                if durable_restart:
                    sequence=state.get('durable_journal') or {}
                    ordered=sequence.get('accepted')==2 and sequence.get('next')==2
                    # Reuse the actual first save command that survived in
                    # the *test client*, not an invented invalid credential.
                    replay=page.evaluate('''async(arg)=>{const r=await fetch('/api/onlyoffice/shadow-stale-probe',{
                      method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
                      body:JSON.stringify({action:'replay-old-editor-revision',
                          userdata:arg.userdata,key:arg.key})});
                      return {status:r.status,body:await r.json()}}''',
                      {'userdata':first_userdata,'key':original_key})
                    check('durable-real-issued-first-command-replay-denied-after-restart',
                          replay['status']==409 and replay['body'].get('stale_rejected') is True and
                          replay['body'].get('bytes_unchanged') is True and
                          get_cell(workbook,'A1')==second_marker)
                else:
                    ordered=(state.get('continuous_commits')==2 and
                             state.get('continuous_seqs')==[1,2])
                check('continuous-two-ordered-server-issued-save-commands',ordered,
                      'server-issued sequences='+str([1,2] if ordered else state.get('continuous_seqs')))
                if durable_restart:
                    check('durable-second-save-journal-persisted-after-restart',
                          sequence.get('accepted')==2 and sequence.get('key')==original_key)
            if final_close_probe:
                # The two force-saves above are already committed. Now edit a
                # third distinct value WITHOUT another force-save and close
                # the last editor. Only the true Document Server status=2
                # callback can save it; a fixture-generated callback cannot
                # satisfy this experiment.
                close_marker=new_marker+'_ON_CLOSE'
                canvas.click(position={'x':min(140,max(10,bounds['width']//3)),
                                       'y':min(120,max(10,bounds['height']//3))},timeout=15000)
                page.keyboard.press('Control+Home')
                page.keyboard.press('F2')
                page.keyboard.press('Control+A')
                page.keyboard.type(close_marker,delay=25)
                page.keyboard.press('Enter')
                time.sleep(3)
                check('final-close-unsaved-change-not-previously-persisted',
                      get_cell(workbook,'A1')==new_marker)
                check('final-close-editor-key-still-original',
                      docs_frame.evaluate('window.MC_DOCUMENT_KEY')==original_key)
                # Navigate the one live editor away; no synthetic status=2
                # callback, no browser reload masquerading as a final close.
                page.goto(base+'/auth/login',wait_until='domcontentloaded',timeout=30000)
                print('MC_ONLYOFFICE_REAL final-close-waiting-for-real-signed-status2 '
                      'deadline=90s',flush=True)
                deadline=time.monotonic()+90
                closure=None
                while time.monotonic()<deadline:
                    closure=page.evaluate('''async()=>{const r=await fetch('/api/onlyoffice/metrics',
                        {credentials:'same-origin'}); if(!r.ok) throw Error('metrics unavailable');
                        return await r.json()}''')
                    if (closure.get('final_close_committed',0)>0 or
                            closure.get('final_close_rejected',0)>0):break
                    time.sleep(2)
                check('final-close-genuine-jwt-authenticated-status2-observed',
                      closure is not None and closure.get('final_close_signed_status2',0)>0,
                      'status2_count='+str((closure or {}).get('final_close_signed_status2',0)))
                print('MC_ONLYOFFICE_REAL FINAL_CLOSE_OBSERVATION '+json.dumps({
                    'signed_status2':closure.get('final_close_signed_status2'),
                    'status2_rejected':closure.get('final_close_rejected'),
                    'status2_committed':closure.get('final_close_committed'),
                    'status2_had_userdata':closure.get('final_close_has_userdata'),
                    'persisted_final_change':get_cell(workbook,'A1')==close_marker
                },sort_keys=True),flush=True)
                check('final-close-last-unsaved-edit-saved-through-source-callback',
                      closure.get('final_close_committed',0)>0 and
                      closure.get('final_close_rejected',0)==0 and
                      get_cell(workbook,'A1')==close_marker,
                      'must be real status=2 save, not the preceding status=6 save')
                new_marker=close_marker
                # The editor has closed; navigate back to launch the new key.
                page.goto(base+'/graphical',wait_until='domcontentloaded',timeout=30000)
            # Verify the workbook is sourced from the changed persisted bytes,
            # not from a browser-only unsaved display state.
            page.reload(wait_until='domcontentloaded',timeout=30000)
            page.frame_locator('#mc-docs').locator('body').wait_for(timeout=45000)
            reopened=next((f for f in page.frames if '/mc-editor' in f.url and
                           f.url.startswith(f'https://{HOST_DOCS}:')),None)
            check('reopened-isolated-editor-origin',reopened is not None)
            reopened.wait_for_function('window.MC_DOC_READY === true',timeout=120000)
            newer_key=reopened.evaluate('window.MC_DOCUMENT_KEY')
            check('reopen-loads-new-workbook-revision',newer_key!=original_key,
                  'document-ready=true; key-rotated=true' if newer_key!=original_key else 'key unchanged')
            check('reopened-workbook-cell-persisted',get_cell(workbook,'A1')==new_marker)
            if require_shadow:
                metrics=page.evaluate('''async()=>{const r=await fetch('/api/onlyoffice/metrics',{
                  credentials:'same-origin'});return {status:r.status,body:await r.json()}}''')
                check('shadow-real-editor-callback-committed-through-revision-fence',
                      metrics['status']==200 and metrics['body'].get('shadow_commits',0)>=1,
                      'source-backed real callback plus guarded final write')
                probe=page.evaluate('''async(arg)=>{const r=await fetch('/api/onlyoffice/shadow-stale-probe',{
                  method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify(arg)});
                  return {status:r.status,body:await r.json()}}''',
                  {'action':'replay-old-editor-revision','userdata':first_userdata,
                   'key':original_key} if durable_restart else
                  {'action':'replay-old-editor-revision'})
                check('shadow-replayed-old-grant-rejected-after-real-editor-save',
                      probe['status']==409 and probe['body'].get('stale_rejected') is True and
                      probe['body'].get('bytes_unchanged') is True and
                      get_cell(workbook,'A1')==new_marker,
                      'a formerly valid editor-base revision cannot overwrite saved content')
            # Preserve the independently proved edit-save-reopen result even if
            # a later security check fails or the operator interrupts the run.
            if on_persisted is not None:on_persisted()
            # Closing the authenticated context finishes its document-origin
            # requests (including long-lived sockets) before reading Nginx's
            # append-only one-bit-per-request audit. Docker exec has a fixed
            # timeout; there are no per-request Playwright round trips.
            context.close()
            docs_checked,docs_cookie_exposed=audit_document_request_cookies(
                read_proxy_document_cookie_audit(compose),baseline_audit_count)
            check('editor-origin-never-received-mc-session-cookie',
                  docs_checked>0 and not docs_cookie_exposed,
                  f'doc-origin-requests={docs_checked}')
            bob=browser.new_context(ignore_https_errors=False)
            try:
                bobpage=bob.new_page()
                bobpage.goto(base+'/graphical',wait_until='domcontentloaded',timeout=30000)
                check('independent-browser-context-login-required',bobpage.url.startswith(base+'/auth/login'))
                # Browser-native fetch preserves Chromium's explicit
                # hostname mapping, unlike bobpage.request (Node-side DNS).
                denied_status=bobpage.evaluate('''async () => {
                    const response=await fetch('/api/onlyoffice/config', {credentials:'include'});
                    return response.status;
                }''')
                check('bob-browser-api-config-denied',denied_status in (401,403),
                      f'HTTP={denied_status}')
                from playwright.sync_api import Error as BrowserError
                unknown=bob.new_page()
                rejection=''
                try:
                    unknown.goto(f'https://unknown.main-computer.local:{port}/graphical',timeout=8000)
                except BrowserError as exc:
                    rejection=str(exc)
                check('browser-unknown-local-namespace-fails-resolution',
                      'ERR_NAME_NOT_RESOLVED' in rejection or 'ERR_DNS' in rejection,
                      'unknown .local host must not use network/mDNS fallback')
            finally:bob.close()
            context.close()
            return True
        finally:browser.close()


def live_proxy_port_is_private(configured, published, port):
    """Both a requested HostConfig mapping AND a live Docker mapping must match.

    Docker can accept HostConfig.PortBindings on an internal-only network
    while leaving NetworkSettings.Ports empty: checking HostConfig alone gives
    a false PASS followed by a long TCP_CONNECTION_REFUSED readiness retry.
    """
    expected={'HostIp':'127.0.0.1','HostPort':str(port)}
    requested=(configured or {}).get('443/tcp') or []
    actual=(published or {}).get('443/tcp') or []
    return (set(configured or {})=={'443/tcp'} and
            len(requested)==1 and requested[0]==expected and
            len(actual)==1 and actual[0]==expected and
            not any(key != '443/tcp' and value for key,value in (published or {}).items()))


def audit_containers(compose,port):
    for service in ('onlyoffice','fixture','proxy'):
        cid=run_command([*compose,'ps','-q',service],timeout=15,capture=True).stdout.strip()
        if not cid:raise RuntimeError('missing container for '+service)
        def inspect(field):
            result=run_command(['docker','inspect','--format','{{json '+field+'}}',cid],
                               timeout=15,capture=True)
            return json.loads(result.stdout)
        running=inspect('.State.Running')
        check(service+'-container-running',running,detail='container started and stayed running')
        configured=inspect('.HostConfig.PortBindings')
        published=inspect('.NetworkSettings.Ports')
        networks=set((inspect('.NetworkSettings.Networks') or {}).keys())
        if service!='proxy':
            check('no-published-ports-'+service,not configured and
                  not any(published.values()) if published else not configured)
            check(service+'-private-network-only',len(networks)==1 and
                  next(iter(networks)).endswith('_sandbox'),
                  detail='backend cannot join host-facing ingress network')
        else:
            check('proxy-dual-network-isolation',len(networks)==2 and
                  any(n.endswith('_ingress') for n in networks) and
                  any(n.endswith('_sandbox') for n in networks))
            check('only-disposable-https-loopback-port-published',
                  live_proxy_port_is_private(configured,published,port),
                  detail='requested AND live 443/tcp bindings must be 127.0.0.1:'+str(port))
    bound=run_command([*compose,'port','proxy','443'],timeout=15,capture=True).stdout.strip()
    check('docker-reports-reachable-https-host-port',bound==f'127.0.0.1:{port}',
          detail='Docker reports '+repr(bound))


def show_network_failure_context(compose):
    """Diagnose the disposable stack without inspecting credentials or production."""
    for label,command in (
        ('container-status',[*compose,'ps','-a']),
        ('proxy-logs',[*compose,'logs','--no-color','--tail','25','proxy']),
        ('host-port',[*compose,'port','proxy','443']),
    ):
        try:
            response=subprocess.run(command,timeout=12,check=False,text=True,
                                    stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            message=(response.stdout+'\n'+response.stderr).strip()
            print('MC_ONLYOFFICE_REAL NETWORK_DIAGNOSTIC '+label+
                  ' exit='+str(response.returncode)+' '+message[:1800],flush=True)
        except Exception as exc:
            print('MC_ONLYOFFICE_REAL NETWORK_DIAGNOSTIC '+label+
                  ' error='+type(exc).__name__,flush=True)


def experiment(tag='latest',browser_executable=None,external_bob_window=0,shadow_revision=False,continuous_saves=False,durable_restart=False,final_close_probe=False):
    if continuous_saves and not shadow_revision:
        raise ValueError('continuous-saves test requires --shadow-revision')
    if durable_restart and not continuous_saves:
        raise ValueError('durable-restart test requires --continuous-saves')
    if final_close_probe and not durable_restart:
        raise ValueError('final-close-probe requires --durable-restart')
    if not shutil.which('docker'):
        print('MC_ONLYOFFICE_REAL UNVERIFIED docker unavailable; no experiment run',flush=True);return 2
    if not re.fullmatch(r'[A-Za-z0-9][\w.\-]{0,95}',tag):
        raise ValueError('unsafe Docker image tag')
    if shadow_revision and not (ROOT/'tools'/'mcel_onlyoffice_shadow_revision.py').is_file():
        raise RuntimeError('shadow revision candidate source missing')
    if durable_restart and not (ROOT/'tools'/'mcel_onlyoffice_durable_save_journal.py').is_file():
        raise RuntimeError('durable journal source missing')
    for file in SOURCE_FILES:
        if not file.is_file():raise RuntimeError('missing required source '+str(file))
    project='mc-oo-real-'+secrets.token_hex(4)
    outcome=2
    real_editor_saved=False
    with tempfile.TemporaryDirectory(prefix='mc-onlyoffice-real-editor-') as temp:
        work=Path(temp)
        (work/'documents').mkdir()
        old='MC_ORIGINAL_'+secrets.token_hex(4)
        new='MC_EDITED_'+secrets.token_hex(8)
        workbook=work/'documents'/'real-edit.xlsx'
        create_workbook(workbook,old)
        check('disposable-xlsx-constructed',get_cell(workbook,'A1')==old)
        certfile,keyfile=certificate(work)
        (work/'nginx.conf').write_text(nginx_config(),encoding='utf8')
        key=secrets.token_bytes(32)
        auth_b64=base64.urlsafe_b64encode(key).decode().rstrip('=')
        # The host-side test file is disposable and has no relation to the
        # user's AAA_AUTH_MAIN_COMPUTER.json proof or their OS user account.
        proof=work/'disposable-AAA_AUTH_MAIN_COMPUTER.json'
        proof.write_text(json.dumps({'type':'mc-proof-v2','key_b64':auth_b64}),encoding='utf8')
        if os.name!='nt':os.chmod(proof,0o600)
        jwt_secret=secrets.token_urlsafe(36)
        port=available_port()
        (work/'docker-compose.json').write_text(json.dumps(manifest(work,port,auth_b64,jwt_secret,tag,shadow_revision,continuous_saves,durable_restart,final_close_probe),indent=2),encoding='utf8')
        compose=['docker','compose','-p',project,'-f',str(work/'docker-compose.json')]
        try:
            run_command(['docker','version','--format','{{.Server.Version}}'],timeout=15)
            run_command([*compose,'config','--quiet'],timeout=30)
            print(f'MC_ONLYOFFICE_REAL project={project} https-port={port} '
                  'production-ports-unchanged=true production-documents-untouched=true',flush=True)
            run_command([*compose,'up','-d','onlyoffice','fixture','proxy'],timeout=600)
            audit_containers(compose,port)
            wait_for_https(port,certfile)
            check('unknown-mc-local-hostname-rejected',
                  https_request(port,'unknown.main-computer.local','/graphical',certfile,verify_hostname=False)==421)
            audit_baseline=len(read_proxy_document_cookie_audit(compose))
            def mark_real_editor_saved():
                nonlocal real_editor_saved
                real_editor_saved=True
            browser_round_trip(port,proof,workbook,new,certfile,compose,
                               audit_baseline,browser_executable,on_persisted=mark_real_editor_saved,
                               require_shadow=shadow_revision,continuous_saves=continuous_saves,
                               durable_restart=durable_restart,
                               final_close_probe=final_close_probe)
            real_editor_saved=True
            bob=subprocess.run([*compose,'run','--rm','--no-deps','bob'],check=False,timeout=100)
            check('credentialless-bob-container-attacks-blocked',bob.returncode==0,
                  f'exit={bob.returncode}')
            if external_bob_window:
                # Opt-in only. Bob runs separately under his genuine Windows SID,
                # with no access to test authority secrets or production state.
                print('MC_ONLYOFFICE_REAL EXTERNAL_WINDOWS_BOB_WINDOW '
                      f'port={port} seconds={external_bob_window} '
                      'run tools.mcel_onlyoffice_pre_cutover_windows_bob as the other Windows account',
                      flush=True)
                time.sleep(external_bob_window)
            print('MC_ONLYOFFICE_REAL SUMMARY '+json.dumps({
                'status':'PASS','real_documentserver_edit_save_reopen':True,
                'shadow_revision_fence':shadow_revision,
                'continuous_saves_in_one_editor':continuous_saves,
                'durable_backend_restart_between_saves':durable_restart,
                'final_close_status2_persisted':final_close_probe,
                'browser_tls_trust':'EPHEMERAL_SPKI_PIN_NOT_WINDOWS_CERT_STORE',
                'production_modified':False,'second_windows_account_tested':False,
                'legacy_production_onlyoffice_routes_changed':False,
                'origins':[HOST_DEV,HOST_DOCS]
            },sort_keys=True),flush=True)
            outcome=0
        except KeyboardInterrupt:
            print('MC_ONLYOFFICE_REAL INTERRUPTED operator-requested cancellation',flush=True)
            print('MC_ONLYOFFICE_REAL SUMMARY '+json.dumps({'status':'INTERRUPTED',
                    'reason':'KEYBOARD_INTERRUPT','production_modified':False,
                    'real_documentserver_edit_save_reopen':real_editor_saved}),flush=True)
            outcome=130
        except AssertionError as exc:
            show_network_failure_context(compose)
            print('MC_ONLYOFFICE_REAL FAIL '+str(exc)[:400],flush=True)
            print('MC_ONLYOFFICE_REAL SUMMARY '+json.dumps({'status':'FAIL',
                    'reason':'ACCEPTANCE_ASSERTION_FAILED','production_modified':False,
                    'real_documentserver_edit_save_reopen':real_editor_saved}),flush=True)
            outcome=1
        except Exception as exc:
            show_network_failure_context(compose)
            print('MC_ONLYOFFICE_REAL UNVERIFIED '+type(exc).__name__+': '+str(exc)[:400],flush=True)
            print('MC_ONLYOFFICE_REAL SUMMARY '+json.dumps({'status':'UNVERIFIED',
                    'reason':type(exc).__name__,'production_modified':False,
                    'real_documentserver_edit_save_reopen':real_editor_saved}),flush=True)
            outcome=2
        finally:
            try:
                run_command([*compose,'down','-v','--remove-orphans'],timeout=180)
                print('MC_ONLYOFFICE_REAL disposable-docker-cleanup PASS',flush=True)
            except Exception as exc:
                print('MC_ONLYOFFICE_REAL disposable-docker-cleanup FAIL '+repr(exc)[:190],flush=True)
                print('MC_ONLYOFFICE_REAL RECOVERY_PROJECT '+project,flush=True)
                outcome=2
    return outcome


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',nargs='?',default='run',choices=['run'])
    p.add_argument('--onlyoffice-tag',default=os.environ.get('MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG','latest'))
    p.add_argument('--browser-executable',default=None)
    p.add_argument('--shadow-revision',action='store_true',
                   help='Run real editor against test-only atomic revision guard')
    p.add_argument('--continuous-saves',action='store_true',
                   help='Prove two signed, ordered real force-saves in one uninterrupted editor')
    p.add_argument('--durable-restart',action='store_true',
                   help='Use durable save journal and restart only fixture between two real saves')
    p.add_argument('--final-close-probe',action='store_true',
                   help='Require a real signed status=2 callback to persist an edit made after both force-saves')
    p.add_argument('--external-bob-window',type=int,default=0,
                   help='Hold disposable Docker HTTPS open for an actual second Windows user')
    args=p.parse_args(argv)
    if not 0<=args.external_bob_window<=1800:p.error('external Bob window must be 0..1800 seconds')
    return experiment(args.onlyoffice_tag,args.browser_executable,args.external_bob_window,
                      args.shadow_revision,args.continuous_saves,args.durable_restart,
                      args.final_close_probe)

if __name__=='__main__':raise SystemExit(main())
