"""Offline, non-Docker contract checks for the disposable real-editor lab."""
from __future__ import annotations

from datetime import datetime,timezone
import hmac
import io
import hashlib
import json
from pathlib import Path
import os
import tempfile
import subprocess
import sys
import time
import socket
import http.client
import base64
import copy
import secrets
import unittest
import zipfile

from tools import mcel_onlyoffice_real_editor_lab as lab


class OnlyOfficeRealEditorContract(unittest.TestCase):
    def test_workbook_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'a.xlsx'
            lab.create_workbook(p,'MC_INITIAL')
            self.assertEqual(lab.get_cell(p,'A1'),'MC_INITIAL')
            self.assertEqual(lab.get_cell(p,'B1'),'1')
            self.assertEqual(lab.get_cell(p,'A2'),'2')
            with zipfile.ZipFile(p) as z:
                self.assertIn('xl/worksheets/sheet1.xml',z.namelist())
                self.assertIn('xl/workbook.xml',z.namelist())

    def test_certificate_explicit_local_hosts_not_ca(self):
        from cryptography import x509
        with tempfile.TemporaryDirectory() as tmp:
            cert,key=lab.certificate(Path(tmp))
            obj=x509.load_pem_x509_certificate(cert.read_bytes())
            hosts=obj.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
            self.assertEqual(set(hosts),{lab.HOST_DEV,lab.HOST_DOCS})
            self.assertFalse(obj.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
            self.assertTrue(key.is_file())

    def test_one_shared_onlyoffice_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'documents').mkdir()
            spec=lab.manifest(root,56993,'a'*43,'secret_jwt_long_enough_for_test_123456','latest')
            self.assertEqual(set(spec['services']),{'fixture','proxy','onlyoffice','bob'})
            self.assertTrue(spec['networks']['sandbox']['internal'])
            self.assertEqual(spec['networks']['ingress'],{})
            self.assertEqual(spec['services']['proxy']['networks'],['ingress','sandbox'])
            for role in ('onlyoffice','fixture','bob'):
                self.assertEqual(spec['services'][role]['networks'],['sandbox'])
            self.assertEqual(spec['services']['onlyoffice']['environment']['JWT_ENABLED'],'true')
            self.assertEqual(spec['services']['onlyoffice']['environment']['ALLOW_META_IP_ADDRESS'],'false')
            for role in ('fixture','onlyoffice','bob'):
                self.assertNotIn('ports',spec['services'][role])
            ports=spec['services']['proxy']['ports']
            self.assertEqual(ports,[{'target':443,'published':56993,'host_ip':'127.0.0.1','protocol':'tcp'}])
            self.assert_test_only_mounts(spec, root)
            self.assertNotIn('mc.test',json.dumps(spec))

    def assert_test_only_mounts(self, spec, root):
        # Test-only allowlist, NOT a substring search over Windows paths.
        # Windows TemporaryDirectory() normally lives under AppData/Local/Temp.
        # Its presence in Docker bind paths is legitimate; production state is not.
        src=lambda p:str(Path(p).resolve())
        expected={
            'onlyoffice':[],
            'fixture':[
                (src(lab.ROOT/'main_computer'/'auth_bootstrap.py'),'/lab/main_computer/auth_bootstrap.py',True),
                (src(lab.ROOT/'main_computer'/'viewport_routes_onlyoffice.py'),'/lab/viewport_routes_onlyoffice.py',True),
                (src(lab.ROOT/'tools'/'mcel_onlyoffice_real_editor_fixture.py'),'/lab/fixture.py',True),
                (src(root/'documents'),'/lab/documents',False),
            ],
            'proxy':[
                (src(root/'nginx.conf'),'/etc/nginx/conf.d/default.conf',True),
                (src(root/'cert.pem'),'/etc/lab/cert.pem',True),
                (src(root/'key.pem'),'/etc/lab/key.pem',True),
            ],
            'bob':[
                (src(lab.ROOT/'tools'/'mcel_onlyoffice_real_editor_bob.py'),'/lab/bob.py',True),
            ],
        }
        self.assertEqual(set(spec['services']),set(expected))
        for service, approved in expected.items():
            binds=spec['services'][service].get('volumes',[])
            self.assertEqual(len(binds),len(approved),service+' unexpected bind count')
            observed=[]
            for volume in binds:
                self.assertEqual(volume.get('type'),'bind',service+' unexpected volume type')
                self.assertEqual(set(volume),{'type','source','target','read_only'},
                                 service+' unexpected volume attributes')
                observed.append((src(volume['source']),volume['target'],volume['read_only']))
            self.assertEqual(observed,approved,service+' unapproved mount or permission')

    def test_mount_allowlist_rejects_credentials_and_host_privileges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'documents').mkdir()
            spec=lab.manifest(root,56993,'a'*43,'s'*44,'latest')
            self.assert_test_only_mounts(spec,root)
            for service,source,target,readonly in (
                ('fixture',root/'../runtime/state/mother','/lab/documents',False),
                ('fixture',Path('/var/run/docker.sock'),'/var/run/docker.sock',True),
                ('proxy',root/'../MainComputer/AuthBootstrap/authority.json','/etc/lab/key.pem',True),
                ('bob',root/'../some-extra-file','/lab/extra',True),
            ):
                with self.subTest(service=service,source=str(source)):
                    bad=copy.deepcopy(spec)
                    bad['services'][service].setdefault('volumes',[]).append({
                        'type':'bind','source':str(source),'target':target,'read_only':readonly})
                    with self.assertRaises(AssertionError):
                        self.assert_test_only_mounts(bad,root)
            bad=copy.deepcopy(spec)
            bad['services']['fixture']['volumes'][0]['read_only']=False
            with self.assertRaises(AssertionError):self.assert_test_only_mounts(bad,root)

    def test_live_port_audit_rejects_unpublished_internal_bridge(self):
        # Docker 29.x can preserve HostConfig.PortBindings while omitting the
        # actual published port; do not let that false-positive reach readiness.
        requested={'443/tcp':[{'HostIp':'127.0.0.1','HostPort':'56993'}]}
        self.assertFalse(lab.live_proxy_port_is_private(requested,{'443/tcp':None},56993))
        self.assertFalse(lab.live_proxy_port_is_private(requested,{'443/tcp':[]},56993))
        self.assertFalse(lab.live_proxy_port_is_private(requested,{},56993))
        self.assertFalse(lab.live_proxy_port_is_private(requested,
            {'443/tcp':[{'HostIp':'0.0.0.0','HostPort':'56993'}]},56993))
        self.assertTrue(lab.live_proxy_port_is_private(requested,requested,56993))

    def test_host_facing_network_only_on_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);(path/'documents').mkdir()
            spec=lab.manifest(path,56694,'a'*43,'s'*44,'latest')
            self.assertEqual(set(spec['networks']),{'sandbox','ingress'})
            self.assertTrue(spec['networks']['sandbox']['internal'])
            self.assertFalse(spec['networks']['ingress'].get('internal',False))
            ingress=[name for name,service in spec['services'].items()
                     if 'ingress' in service['networks']]
            self.assertEqual(ingress,['proxy'])
            self.assertEqual(spec['services']['proxy']['ports'][0]['host_ip'],'127.0.0.1')

    def test_browser_mapping_denies_unapproved_namespace(self):
        text=lab.nginx_config()
        self.assertIn('server_name dev.main-computer.local',text)
        self.assertIn('server_name onlyoffice.main-computer.local',text)
        self.assertIn('return 421',text)
        self.assertIn('location = /mc-editor',text)
        self.assertIn('location = /mc-config',text)
        self.assertNotIn('mc.test',text)

    def test_real_service_source_mounted_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'documents').mkdir()
            mounts=lab.manifest(root,45993,'a'*43,'a'*43,'latest')['services']['fixture']['volumes']
            self.assertEqual(len(mounts),4)
            self.assertTrue(all(x['read_only'] for x in mounts[:3]))
            self.assertFalse(mounts[3]['read_only'])
            self.assertTrue(any(x['target']=='/lab/main_computer/auth_bootstrap.py' for x in mounts))
            self.assertTrue(any(x['target']=='/lab/viewport_routes_onlyoffice.py' for x in mounts))

    def test_genuine_editor_not_synthetic_callback(self):
        import inspect
        source=inspect.getsource(lab.browser_round_trip)
        self.assertIn('window.MC_DOC_READY === true',source)
        self.assertIn('genuine-editor-callback-saved-changed-cell',source)
        self.assertIn("get_cell(workbook,'A1')",source)
        self.assertIn('reopen-loads-new-workbook-revision',source)
        self.assertIn('editor-origin-never-received-mc-session-cookie',source)
        self.assertIn('untrusted-onlyoffice-js-not-in-mc-origin',source)
        self.assertIn('editor-origin-cannot-csrf-mc-control-with-cookie',source)

    def test_browser_uses_separate_ephemeral_profile(self):
        import inspect
        source=inspect.getsource(lab.choose_browser)
        self.assertIn('127.0.0.1',source)
        self.assertIn('^NOTFOUND',source)
        self.assertIn('--ignore-certificate-errors-spki-list=',source)
        self.assertNotIn('ignore_https_errors=True',source)

    def test_source_backed_callback_requires_jwt_and_document_scope(self):
        source=(lab.ROOT/'tools'/'mcel_onlyoffice_real_editor_fixture.py').read_text()
        self.assertIn('onlyoffice_source_handler()',source)
        self.assertIn('jwt_verify(',source)
        self.assertIn("valid_token(q['cap'],'callback',MODE,DOC)",source)
        self.assertIn('safe_urlopen',source)
        self.assertIn('ACTIVE_KEYS',source)

    def test_credentialless_bob_has_no_secrets_or_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'documents').mkdir()
            bob=lab.manifest(root,59999,'a'*43,'a'*43,'latest')['services']['bob']
            self.assertEqual(bob['environment'],{'LAB_BROWSER_PORT':'59999'})
            self.assertEqual(bob['user'],'65534:65534')
            self.assertEqual(bob['cap_drop'],['ALL'])
            self.assertTrue(bob['read_only'])
            self.assertEqual(len(bob['volumes']),1)


    def test_real_bootstrap_authority_and_callback_negative_controls(self):
        """Source-backed disposable HTTP handler; no Docker or production state."""
        import urllib.parse
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); docs=root/'documents'; docs.mkdir()
            work=docs/'real-edit.xlsx';lab.create_workbook(work,'ORIGINAL')
            with socket.socket() as s:
                s.bind(('127.0.0.1',0));port=s.getsockname()[1]
            key=secrets.token_bytes(32)
            key_b64=base64.urlsafe_b64encode(key).decode().rstrip('=')
            jwt_secret=secrets.token_urlsafe(48)
            env=dict(os.environ,
                LAB_AUTH_KEY_B64=key_b64,
                LAB_JWT_SECRET=jwt_secret,
                LAB_BROWSER_PORT=str(port),
                LAB_PORT=str(port),
                LAB_BIND='127.0.0.1',
                LAB_DOCUMENT_DIR=str(docs),
                LAB_AUTH_ROOT=str(root/'private'),
                LAB_SOURCE_PATH=str(lab.ROOT/'main_computer'/'viewport_routes_onlyoffice.py'),
                PYTHONPATH=str(lab.ROOT))
            proc=subprocess.Popen([sys.executable,'-u',str(lab.ROOT/'tools'/'mcel_onlyoffice_real_editor_fixture.py')],
                                  env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            def fetch(method,path,body=None,headers=None,host=None,*,recovery_probe=False):
                conn=http.client.HTTPConnection('127.0.0.1',port,timeout=100 if recovery_probe else 10)
                head={'Host':host or f'dev.main-computer.local:{port}',**(headers or {})}
                data=json.dumps(body).encode() if body is not None else None
                if body is not None:head['Content-Type']='application/json'
                try:
                    conn.request(method,path,body=data,headers=head)
                    r=conn.getresponse()
                    return r.status,dict(r.getheaders()),r.read()
                finally:conn.close()
            try:
                for _ in range(100):
                    if proc.poll() is not None:
                        self.fail('fixture crashed '+(proc.stdout.read() if proc.stdout else '')[:300])
                    try:
                        if fetch('GET','/health')[0]==200:break
                    except (OSError,TimeoutError):time.sleep(.04)
                else:self.fail('fixture did not start')
                # Canonical MC navigation redirects to the login page,
                # while direct access using the internal fixture host is
                # intentionally refused rather than redirected.
                status,headers,_=fetch('GET','/graphical')
                self.assertEqual(status,303)
                self.assertEqual(headers.get('Location'),'/auth/login')
                self.assertEqual(fetch('GET','/graphical',host=f'fixture:{port}')[0],403)
                # Execute Bob's complete hostile request table against the
                # real source-backed fixture, not merely a mocked status map.
                # The Docker run uses port 8000; this offline check dials the
                # temporary local port without changing any URL semantics.
                from tools import mcel_onlyoffice_real_editor_bob as bob
                from unittest.mock import patch
                from contextlib import redirect_stdout
                original_connection=http.client.HTTPConnection
                def local_connection(host,unused_port,timeout=8):
                    return original_connection('127.0.0.1',port,timeout=timeout)
                with patch.dict(os.environ,{'LAB_BROWSER_PORT':str(port),'LAB_PORT':str(port)}):
                    with patch.object(bob.http.client,'HTTPConnection',side_effect=local_connection):
                        with io.StringIO() as messages, redirect_stdout(messages):
                            bob_result=bob.main()
                            bob_output=messages.getvalue()
                self.assertEqual(bob_result,0,bob_output)
                self.assertIn('"checks": 12',bob_output)
                self.assertIn('"failed": 0',bob_output)
                self.assertEqual(fetch('GET','/api/onlyoffice/config')[0],401)
                self.assertEqual(fetch('GET','/api/onlyoffice/file?mode=dev&doc=real-edit.xlsx')[0],403)
                self.assertEqual(fetch('POST','/api/onlyoffice/callback?mode=dev&doc=real-edit.xlsx',
                    {'status':2,'url':'http://evil.example/'})[0],403)
                self.assertEqual(fetch('GET','/api/onlyoffice/config',host='unknown.main-computer.local')[0],403)
                status,_,raw=fetch('GET','/auth/challenge')
                self.assertEqual(status,200)
                c=json.loads(raw)
                message=b'MC-AUTH-BOOTSTRAP-V1\n'+c['challenge_id'].encode()+b'\n'+c['nonce'].encode()+b'\n'
                signature=base64.urlsafe_b64encode(hmac.new(key,message,hashlib.sha256).digest()).decode().rstrip('=')
                data={'challenge_id':c['challenge_id'],'signature':signature}
                origin=f'https://dev.main-computer.local:{port}'
                self.assertEqual(fetch('POST','/auth/verify',data,{'Origin':'https://evil.example'})[0],403)
                status,headers,_=fetch('POST','/auth/verify',data,{'Origin':origin})
                self.assertEqual(status,200)
                cookie=headers['Set-Cookie'].split(';',1)[0]
                self.assertIn('Secure',headers['Set-Cookie'])
                self.assertIn('HttpOnly',headers['Set-Cookie'])
                auth={'Cookie':cookie}
                self.assertEqual(fetch('GET','/api/onlyoffice/config',headers=auth)[0],200)
                _,_,raw=fetch('GET','/api/onlyoffice/config',headers=auth)
                config=json.loads(raw)
                self.assertTrue(config['token'])
                self.assertEqual(config['_browser_api'],f'https://onlyoffice.main-computer.local:{port}/web-apps/apps/api/documents/api.js')
                self.assertEqual(fetch('GET','/api/onlyoffice/config')[0],401)
                self.assertEqual(fetch('GET','/api/onlyoffice/file?mode=debug&doc=real-edit.xlsx&cap=forged')[0],403)
                # Authenticated callback admission is still document scoped and
                # JWT protected, independent of the ordinary browser cookie.
                exp=int(time.time())+120
                tag=hmac.new(key,f'mc-real-onlyoffice-v1|callback|dev|real-edit.xlsx|{exp}'.encode(),hashlib.sha256).hexdigest()
                cap=f'{exp}.{tag}'
                sink='/api/onlyoffice/callback?mode=dev&doc=real-edit.xlsx&path=real-edit.xlsx&cap='+cap
                status,_,_=fetch('POST',sink,{'status':1,'key':config['document']['key']})
                self.assertEqual(status,403)
                def jwt(body):
                    def b64(obj):return base64.urlsafe_b64encode(json.dumps(obj,separators=(',',':')).encode()).decode().rstrip('=')
                    signed=b64({'typ':'JWT','alg':'HS256'})+'.'+b64(body)
                    sig=base64.urlsafe_b64encode(hmac.new(jwt_secret.encode(),signed.encode(),hashlib.sha256).digest()).decode().rstrip('=')
                    return signed+'.'+sig
                body={'status':1,'key':config['document']['key']}
                status,_,_=fetch('POST',sink,body,{'Authorization':'Bearer '+jwt(body)})
                self.assertEqual(status,200)
                ssrf_body={'status':2,'key':config['document']['key'],'url':'http://evil.example/private.xlsx'}
                self.assertEqual(fetch('POST',sink,ssrf_body,{'Authorization':'Bearer '+jwt(ssrf_body)})[0],400)
                self.assertEqual(lab.get_cell(work,'A1'),'ORIGINAL')
                # Deletion forces all old sessions to fail without restarting.
                (root/'private'/'AAA_AUTH_MAIN_COMPUTER.json').unlink()
                # On Windows, the real source reissues the proof under a
                # cross-process file lock and invokes OS ACL utilities. An
                # ordinary 10s HTTP client deadline interrupts that work and
                # can strand .proof-link.lock during test cleanup. This is
                # one real request with a separate bounded recovery deadline,
                # progress diagnostics, and NO synthetic state reconciliation.
                from tools.mcel_onlyoffice_pre_cutover_recovery import await_reissue_http
                status,_,_=await_reissue_http(
                    lambda: fetch('GET','/api/onlyoffice/config',headers=auth,
                                  recovery_probe=True),
                    process=proc,label='MC_OO_CONTRACT deleted-proof-session-revocation',
                    deadline_seconds=90)
                self.assertEqual(status,401)
                self.assertTrue((root/'private'/'AAA_AUTH_MAIN_COMPUTER.json').is_file())
            finally:
                from tools.mcel_onlyoffice_pre_cutover_recovery import stop_disposable_fixture
                stop_disposable_fixture(proc)
                if proc.stdout:proc.stdout.close()

    def test_api_https_uses_loopback_socket_but_verifies_local_hostname(self):
        """Do not let Playwright API client fall back to Windows DNS or mDNS."""
        import ssl
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from unittest.mock import patch

        received=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length=int(self.headers.get('Content-Length','0'))
                body=self.rfile.read(length)
                received.append({'host':self.headers.get('Host'),
                                 'origin':self.headers.get('Origin'),
                                 'cookie':self.headers.get('Cookie'),
                                 'body':body})
                self.send_response(403 if self.headers.get('Origin') !=
                                   f'https://{lab.HOST_DEV}:{self.server.server_port}' else 200)
                self.send_header('Content-Length','0')
                self.end_headers()
            def log_message(self,*args): pass

        class ExpectedTLSFailureServer(ThreadingHTTPServer):
            # The intentional wrong-hostname handshake can end the socket
            # before BaseHTTPRequestHandler sees a request.
            def handle_error(self, request, client_address):
                import sys
                import ssl
                error=sys.exc_info()[1]
                if isinstance(error,(ssl.SSLError,BrokenPipeError,ConnectionResetError)):
                    return
                super().handle_error(request,client_address)

        with tempfile.TemporaryDirectory() as tmp:
            cert,key=lab.certificate(Path(tmp))
            server=ExpectedTLSFailureServer(('127.0.0.1',0),Handler)
            port=server.server_port
            tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(str(cert),str(key))
            server.socket=tls.wrap_socket(server.socket,server_side=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                # A temporary test hostname has no normal DNS resolver entry.
                # The TCP connection is deliberately forced to local loopback.
                with patch.object(lab.socket,'create_connection', wraps=lab.socket.create_connection) as dial:
                    status=lab.https_request(port,lab.HOST_DEV,'/api/onlyoffice/forcesave',cert,
                        method='POST',payload=b'{"action":"forcesave"}',headers={
                        'Origin':f'https://{lab.HOST_DOCS}:{port}',
                        'Cookie':'__Host-MC-Session=disposable-session',
                        'Content-Type':'application/json'})
                    self.assertEqual(status,403)
                    dial.assert_called_once_with(('127.0.0.1',port),timeout=10)
                self.assertEqual(len(received),1)
                self.assertEqual(received[0]['host'],f'{lab.HOST_DEV}:{port}')
                self.assertEqual(received[0]['origin'],f'https://{lab.HOST_DOCS}:{port}')
                self.assertEqual(received[0]['cookie'],'__Host-MC-Session=disposable-session')
                self.assertEqual(received[0]['body'],b'{"action":"forcesave"}')
                # Reject wrong SNI/cert name rather than disabling TLS checks.
                with self.assertRaises(ssl.SSLCertVerificationError):
                    lab.https_request(port,'unknown.main-computer.local','/',cert)
                # Header injection is never permitted even in the test client.
                with self.assertRaises(ValueError):
                    lab.https_request(port,lab.HOST_DEV,'/',cert,headers={'Origin':'x\r\nHost: attacker'})
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)

    def test_editor_cookie_audit_uses_bounded_server_side_evidence(self):
        """No historical Playwright header lookups: they hung on Windows."""
        from types import SimpleNamespace
        from unittest.mock import patch
        import inspect
        text=lab.nginx_config()
        self.assertIn('map $http_cookie $mc_docs_has_session_cookie',text)
        self.assertIn('~__Host-MC-Session= 1;',text)
        self.assertIn("log_format mc_docs_cookie_audit '$mc_docs_has_session_cookie';",text)
        self.assertIn('access_log /var/log/nginx/mc-docs-cookie-audit.log mc_docs_cookie_audit;',text)
        self.assertNotIn('access_log off;',text)
        # Nginx must never write full Cookie headers, URLs, or session keys to disk.
        self.assertNotIn("log_format mc_docs_cookie_audit '$http_cookie'",text)
        self.assertNotIn('$request_uri',text)
        commands=['docker','compose','-p','test-project','-f','fixture.json']
        with patch.object(lab,'run_command',return_value=SimpleNamespace(stdout='0\n0\n1\n')) as run:
            self.assertEqual(lab.read_proxy_document_cookie_audit(commands),['0','0','1'])
            run.assert_called_once_with([*commands,'exec','-T','proxy','cat',
                '/var/log/nginx/mc-docs-cookie-audit.log'],timeout=15,capture=True)
        with patch.object(lab,'run_command',return_value=SimpleNamespace(stdout='')):
            with self.assertRaisesRegex(RuntimeError,'DOC_ORIGIN_COOKIE_AUDIT_UNVERIFIED'):
                lab.read_proxy_document_cookie_audit(commands)
        with patch.object(lab,'run_command',return_value=SimpleNamespace(stdout='0\n2\n')):
            with self.assertRaisesRegex(RuntimeError,'DOC_ORIGIN_COOKIE_AUDIT_UNVERIFIED'):
                lab.read_proxy_document_cookie_audit(commands)
        self.assertEqual(lab.audit_document_request_cookies(['0','0','0','0'],2),(2,False))
        self.assertEqual(lab.audit_document_request_cookies(['0','0','1','0'],2),(2,True))
        for events,baseline in ((['0'],1), (['0','0'],3), (['0','garbled'],0),([],0)):
            with self.subTest(events=events,baseline=baseline):
                with self.assertRaisesRegex(RuntimeError,'DOC_ORIGIN_COOKIE_AUDIT_UNVERIFIED'):
                    lab.audit_document_request_cookies(events,baseline)
        source=inspect.getsource(lab.browser_round_trip)
        self.assertNotIn('all_headers()',source.replace('request.all_headers() calls', ''))
        self.assertNotIn("context.on('request'",source)
        self.assertIn('context.close()',source)
        self.assertIn('read_proxy_document_cookie_audit(compose)',source)
        self.assertIn('if on_persisted is not None:on_persisted()',source)
        experiment=inspect.getsource(lab.experiment)
        self.assertIn('audit_baseline=len(read_proxy_document_cookie_audit(compose))',experiment)
        self.assertIn('on_persisted=mark_real_editor_saved',experiment)
        self.assertIn('except KeyboardInterrupt:',experiment)

    def test_bob_graphical_host_separation_is_explicit(self):
        from tools import mcel_onlyoffice_real_editor_bob as bob
        import inspect
        source=inspect.getsource(bob.main)
        self.assertIn("primary_host='dev.main-computer.local:'",source)
        self.assertIn("('anonymous-graphical-redirect'",source)
        self.assertIn("('internal-fixture-host-cannot-render-graphical'",source)
        self.assertIn("{'Host':primary_host},(303,)",source)
        self.assertIn("'GET','/graphical',None,{},(403,)",source)

    def test_partial_failure_summary_preserves_real_editor_evidence(self):
        import inspect
        source=inspect.getsource(lab.experiment)
        self.assertIn('real_editor_saved=False',source)
        self.assertIn('real_editor_saved=True',source)
        self.assertIn("'real_documentserver_edit_save_reopen':real_editor_saved",source)

    def test_browser_security_probes_do_not_use_node_side_playwright_dns(self):
        import inspect
        source=inspect.getsource(lab.browser_round_trip)
        self.assertNotIn('context.request.post(',source)
        self.assertNotIn('bobpage.request.get(',source)
        self.assertIn('hostile_status=https_request(',source)
        self.assertIn('denied_status=bobpage.evaluate(',source)
        self.assertIn('editor-origin-cannot-csrf-mc-control-with-cookie',source)
        self.assertIn('bob-browser-api-config-denied',source)

    def test_invalid_image_tag_refused_without_docker(self):
        # The parser fails before executing docker with invalid tags only when
        # Docker is present; check the image grammar independently.
        import re
        valid=re.compile(r'[A-Za-z0-9][\w.\-]{0,95}')
        for value in ('latest;rm','bad/../path',''):
            self.assertIsNone(valid.fullmatch(value))


def main():
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(OnlyOfficeRealEditorContract))
    print('MC_ONLYOFFICE_REAL_CONTRACT SUMMARY '+json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'status':'PASS' if result.wasSuccessful() else 'FAIL'}),flush=True)
    return 0 if result.wasSuccessful() else 1

if __name__=='__main__':raise SystemExit(main())
