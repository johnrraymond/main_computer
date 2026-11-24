"""Regression: venv listener ownership, bounded migration, and actual browser framing."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from main_computer import onlyoffice_deployment as d
from tools import mcel_onlyoffice_deployment as tool


class ListenerOwnership(unittest.TestCase):
    def setUp(self):
        self.root=tool.ROOT
        self.module='main_computer.app_control'
        self.command=[r'C:\venv\Scripts\python.exe','-m',self.module,'--root',str(self.root),'run']
        self.child={'state':'running','pid':24016,'command':self.command}
        def row(pid,parent,exe,created,args):
            return {'ProcessId':pid,'ParentProcessId':parent,'ExecutablePath':exe,'Created':created,'argv':args}
        self.processes={2220:row(2220,12108,r'C:\Python312\python.exe',100,['python','-m','main_computer.app_control','bootstrap']),
            24016:row(24016,2220,self.command[0],200,self.command),
            33008:row(33008,24016,r'C:\Python312\python.exe',300,[r'C:\Python312\python.exe',*self.command[1:]])}

    def accepted(self, owner=33008):
        return tool.supervised_listener(self.child,2220,owner,self.processes,self.module,self.root)

    def test_observed_windows_redirector_and_direct_listener(self):
        self.assertTrue(self.accepted());self.assertTrue(self.accepted(24016))

    def test_unrelated_or_wrong_command_listener_denied(self):
        self.processes[33008]['ParentProcessId']=99999
        self.assertFalse(self.accepted())
        self.processes[33008]['ParentProcessId']=24016
        self.processes[33008]['argv'][-1]='other-command'
        self.assertFalse(self.accepted())

    def test_pid_reuse_and_missing_identity_denied(self):
        self.processes[33008]['Created']=150
        self.assertFalse(self.accepted())
        self.processes[33008]['Created']=300
        self.processes[33008]['argv']=[]
        self.assertFalse(self.accepted())

    def test_unrelated_supervisor_or_exited_child_denied(self):
        self.processes[24016]['ParentProcessId']=777
        self.assertFalse(self.accepted())
        self.processes[24016]['ParentProcessId']=2220
        self.child['state']='exited'
        self.assertFalse(self.accepted())

    def test_auth_module_redirector(self):
        self.module='main_computer.auth_bootstrap_service_deployment'
        for args in (self.command,self.processes[33008]['argv']): args[2]=self.module
        self.assertTrue(self.accepted())


class Migration(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.state=Path(temp.name);self.root=tool.ROOT
        self.manifest=json.loads((self.root/'tools/onlyoffice-deployment-frame-fix.json').read_text())
        self.new=d.file_hashes(self.root,tool.ALL_FILES)
        self.new.update(self.manifest['corrected'])
        self.policy={'schema':1,'mode':'hardened','primary':'dev','roots':{'dev':str(self.root)},
                     'fingerprint':{**self.new,**self.manifest['previous']},'image_id':'sha256:unchanged','image_tag':'latest'}
        (self.state/d.POLICY_NAME).write_text(json.dumps(self.policy))
        self.opts=SimpleNamespace(authorize_cutover=True)

    def migrate(self):
        with patch.object(tool,'no_supervisors'),patch.object(tool,'source_evidence') as evidence, patch.object(d,'file_hashes',return_value=self.new):
            tool.migrate_frame_fix(self.state,self.root,self.opts)
            evidence.assert_called_once_with(self.root)

    def test_exact_update_preserves_selection_and_other_fields(self):
        self.migrate()
        self.assertEqual(d.read_policy(self.state),{**self.policy,'fingerprint':self.new})
        self.migrate() # idempotent, still requires quiescence and evidence

    def test_unrelated_source_change_cannot_be_rebound(self):
        name=next(k for k in self.new if k not in self.manifest['corrected'])
        self.policy['fingerprint'][name]='0'*64
        (self.state/d.POLICY_NAME).write_text(json.dumps(self.policy))
        with self.assertRaisesRegex(RuntimeError,'FRAME_FIX_SOURCE_MISMATCH'): self.migrate()
        self.assertEqual(d.read_policy(self.state),self.policy)

    def test_unrecognized_baseline_or_unauthorized_update_denied(self):
        self.opts.authorize_cutover=False
        with self.assertRaisesRegex(RuntimeError,'EXPLICIT_CUTOVER'):self.migrate()
        self.opts.authorize_cutover=True
        self.policy['fingerprint'][next(iter(self.manifest['previous']))]='0'*64
        (self.state/d.POLICY_NAME).write_text(json.dumps(self.policy))
        with self.assertRaisesRegex(RuntimeError,'UNRECOGNIZED_FRAME_FIX_BASELINE'):self.migrate()


class BrowserFraming(unittest.TestCase):
    def test_real_registered_tls_frame_headers_and_windows_browser(self):
        code=r'''
import base64, hashlib, os, shutil, tempfile
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from playwright.sync_api import sync_playwright
from main_computer.auth_bootstrap import BootstrapAuthority, BootstrapServer, generate_tls_certificate, provision
from main_computer.auth_bootstrap_service_deployment import RegisteredHandler
from main_computer.auth_hostnames import managed_browser_resolver_rules
from main_computer.hardened_ingress import AssertionVerifier, admit
from tools.mcel_onlyoffice_cutover import ProbeHandler, running, request, auth_cookie, DEV, DEBUG
class App(ProbeHandler):
    def do_GET(self):
        if not admit(self,'GET'):return
        route=urlsplit(self.path).path
        if route=='/applications/code-editor':
            if 'mcel-shell-child=1' in self.path:
                body=b'<html><body><div id="code-editor-app">native child</div></body></html>'
            else:
                body=b'<html><body><iframe id="mcel-shell-frame" src="/applications/code-editor?mcel-shell-child=1"></iframe></body></html>'
        else:body=b'<html><body>other</body></html>'
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
with tempfile.TemporaryDirectory() as tmp:
    state=Path(tmp)/'auth';proof=provision(state);cert,key,_=generate_tls_certificate(state);authority=BootstrapAuthority(state)
    with running(ThreadingHTTPServer(('127.0.0.1',0),App)) as app:
        app.mc_gateway_verifier=AssertionVerifier(authority)
        with running(BootstrapServer(('127.0.0.1',0),authority,cert=cert,key=key,isolated_backend_ports={'dev':app.server_port,'debug':app.server_port})) as server:
            server.RequestHandlerClass=RegisteredHandler;n=server.server_port
            child='/applications/code-editor?mcel-shell-child=1'
            status,headers,_=request(DEV,n,cert,child);assert status==401 and headers['X-Frame-Options']=='DENY'
            status,headers,_=request(DEV,n,cert,'/auth/login');assert status==200 and headers['X-Frame-Options']=='DENY' and "frame-ancestors 'none'" in headers['Content-Security-Policy']
            cookie=auth_cookie(server,proof,cert,host=DEV)
            status,headers,_=request(DEV,n,cert,child,cookie=cookie);assert status==200 and headers['X-Frame-Options']=='SAMEORIGIN' and headers['Content-Security-Policy']=="frame-ancestors 'self'"
            status,headers,_=request(DEV,n,cert,'/api/private',cookie=cookie);assert status==200 and headers['X-Frame-Options']=='DENY'
            status,headers,_=request('onlyoffice.main-computer.localhost',n,cert,child,cookie=cookie);assert status==403 and headers['X-Frame-Options']=='DENY'
            name,token=cookie.split('=',1)
            print('REGISTERED_TLS_FRAME_HEADERS_PASS')
            if os.name=='nt' or os.getenv('MC_OO_TEST_LOCAL_CHROMIUM')=='1':
                leaf=x509.load_pem_x509_certificate(cert.read_bytes());spki=leaf.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
                pin=base64.b64encode(hashlib.sha256(spki).digest()).decode()
                with sync_playwright() as pw:
                    kwargs={'channel':'msedge'} if os.name=='nt' else {'executable_path':shutil.which('chromium') or shutil.which('google-chrome')}
                    browser=pw.chromium.launch(**kwargs,headless=True,args=['--ignore-certificate-errors-spki-list='+pin,'--no-proxy-server'])
                    try:
                        context=browser.new_context();origin=f'https://{DEV}:{n}';debug=f'https://{DEBUG}:{n}'
                        context.add_cookies([{'name':name,'value':token,'url':origin,'secure':True,'httpOnly':True,'sameSite':'Strict'}, {'name':name,'value':token,'url':debug,'secure':True,'httpOnly':True,'sameSite':'Strict'}])
                        page=context.new_page();page.goto(origin+'/applications/code-editor',wait_until='domcontentloaded',timeout=120000)
                        page.frame_locator('#mcel-shell-frame').locator('#code-editor-app').wait_for(state='visible',timeout=120000)
                        assert page.frame_locator('#mcel-shell-frame').locator('#code-editor-app').inner_text()=='native child'
                        page.goto(debug+'/applications/code-editor',wait_until='domcontentloaded',timeout=120000)
                        with page.expect_console_message(predicate=lambda m: 'frame-ancestors' in m.text or 'X-Frame-Options' in m.text,timeout=120000):
                            page.locator('#mcel-shell-frame').evaluate('(el,url)=>el.src=url',origin+child)
                        page.frame_locator('#mcel-shell-frame').locator('#code-editor-app').wait_for(state='hidden',timeout=120000)
                    finally:browser.close()
                print('REGISTERED_SAME_ORIGIN_BROWSER_FRAME_PASS')
            authority.sessions.revoke(token)
            status,headers,_=request(DEV,n,cert,child,cookie=cookie);assert status==401 and headers['X-Frame-Options']=='DENY'
'''
        env={**os.environ,'MAIN_COMPUTER_HARDENED_CANDIDATE':'1','MAIN_COMPUTER_PRIMARY_OWNER':'dev','MAIN_COMPUTER_INSTALLATION_MODE':'dev'}
        run=subprocess.run([sys.executable,'-c',code],cwd=tool.ROOT,env=env,capture_output=True,text=True,timeout=900 if os.name=='nt' else 180)
        self.assertEqual(run.returncode,0,run.stdout+'\n'+run.stderr)
        self.assertIn('REGISTERED_TLS_FRAME_HEADERS_PASS',run.stdout)
        if os.name=='nt' or os.getenv('MC_OO_TEST_LOCAL_CHROMIUM')=='1':
            self.assertIn('REGISTERED_SAME_ORIGIN_BROWSER_FRAME_PASS',run.stdout)

if __name__=='__main__':unittest.main()
