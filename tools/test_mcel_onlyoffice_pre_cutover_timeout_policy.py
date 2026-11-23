"""Fail-closed checks for Windows concurrency and real Docker outage probes.

No installed MC service, production document, Windows trust store or Docker
container is touched.  An optional local Nginx fixture tests the exact 502
behavior against an intentionally absent upstream.
"""
from __future__ import annotations

import inspect
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unittest

from tools import mcel_onlyoffice_real_editor_lab as lab
from tools import mcel_onlyoffice_pre_cutover_sessions as sessions
from tools import mcel_onlyoffice_pre_cutover_certification as cert
from tools import mcel_onlyoffice_pre_cutover_docker_faults as faults


class OnlyOfficeTimeoutPolicy(unittest.TestCase):
    def test_real_concurrent_child_processes_bounded_individually(self):
        source=inspect.getsource(sessions.run)
        self.assertIn('ThreadPoolExecutor(max_workers=4)',source)
        self.assertIn("spawn('issue'),range(8)",source)
        self.assertIn('timeout=120',source)
        self.assertIn('SESSION_CHILD_TIMEOUT',source)
        self.assertIn('concurrent-issuance START',source)
        self.assertIn('concurrent-issuance COMPLETED',source)

    def test_certification_allows_bounded_child_work_to_complete(self):
        source=inspect.getsource(cert.run)
        start=source.index("sessions=stage('offline-cross-process-sessions'")
        self.assertIn('timeout=600',source[start:start+650])
        self.assertIn("s.get('checks')==15",source)

    def test_outage_evidence_requires_real_http_error_not_socket_timeout(self):
        source=inspect.getsource(faults.experiment)
        self.assertIn("'documentserver-is-really-stopped'",source)
        self.assertIn('OUTAGE_HTTPS_RESPONSE_UNVERIFIED',source)
        self.assertIn('st in (502,503,504) and elapsed<10',source)
        self.assertIn("'document-server-outage-fails-closed'",source)

    def test_docs_readiness_timeout_is_short_but_editor_streams_are_not(self):
        conf=lab.nginx_config()
        health=conf.split('location = /healthcheck {',1)[1].split('}',1)[0]
        stream=conf.split('location / { proxy_pass http://onlyoffice:80;',1)[1].split('}',1)[0]
        self.assertIn('proxy_pass http://onlyoffice:80/healthcheck;',health)
        for directive in ('proxy_connect_timeout 3s;', 'proxy_read_timeout 3s;', 'proxy_send_timeout 3s;'):
            self.assertIn(directive,health)
        self.assertIn('proxy_read_timeout 600s;',stream)
        self.assertIn('proxy_send_timeout 600s;',stream)
        self.assertNotIn('proxy_read_timeout 3s;',stream)

    @unittest.skipUnless(shutil.which('nginx'),'Nginx binary not installed')
    def test_real_nginx_returns_502_promptly_for_absent_documentserver(self):
        """A dead backend must produce observable HTTP 502, not a client timeout."""
        with tempfile.TemporaryDirectory(prefix='mc-oo-nginx-outage-') as directory:
            root=Path(directory)
            certfile,keyfile=lab.certificate(root)
            port=lab.available_port()
            dead_port=lab.available_port()
            config=lab.nginx_config()
            config=config.replace('listen 443 ssl','listen '+str(port)+' ssl')
            config=config.replace('/etc/lab/cert.pem',str(certfile)).replace('/etc/lab/key.pem',str(keyfile))
            config=config.replace('/var/log/nginx/mc-docs-cookie-audit.log',str(root/'audit.log'))
            config=config.replace('http://onlyoffice:80','http://127.0.0.1:'+str(dead_port))
            config=config.replace('http://fixture:8000','http://127.0.0.1:'+str(dead_port))
            config=('pid '+str(root/'nginx.pid')+';\n'
                    'error_log '+str(root/'nginx-error.log')+' notice;\n'
                    'events { worker_connections 64; }\nhttp {\n'+config+'\n}\n')
            nginx_conf=root/'nginx.conf';nginx_conf.write_text(config,encoding='utf8')
            test=subprocess.run(['nginx','-t','-c',str(nginx_conf),'-p',str(root)],
                capture_output=True,text=True,timeout=8)
            self.assertEqual(test.returncode,0,test.stderr)
            process=subprocess.Popen(['nginx','-c',str(nginx_conf),'-p',str(root),'-g','daemon off;'],
                stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
            try:
                observed=None
                for _ in range(30):
                    if process.poll() is not None:
                        self.fail('Nginx fixture exited unexpectedly: '+process.stderr.read()[-1000:])
                    try:
                        start=time.monotonic()
                        observed=lab.https_request(port,lab.HOST_DOCS,'/healthcheck',certfile)
                        elapsed=time.monotonic()-start
                        break
                    except (OSError,TimeoutError,ssl.SSLError):
                        time.sleep(.1)
                self.assertEqual(observed,502)
                self.assertLess(elapsed,8,'OnlyOffice dead-health proxy took too long')
            finally:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=5)
                if process.stderr is not None:process.stderr.close()

if __name__=='__main__':unittest.main()
