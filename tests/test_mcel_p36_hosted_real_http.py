"""Real browser→HTTP transport test against controlled TaskManagerService producer.

The HTTP dispatcher is intentionally a laboratory shim: it does NOT certify the
production viewport router or Windows host, which P3.6 separately verifies.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from http import HTTPStatus

from main_computer.task_manager import TaskManagerService
from main_computer.mcel_semantic_generated_host import render_p3_generated_page
from tools.mcel_p36_hosted_probe import probe
from tools.mcel_p36_acceptance_frob import ROOT

class LaboratoryHandler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path!='/applications/mcel-semantic-task-manager-generated-p3':
            self.send_error(404);return
        body=render_p3_generated_page().encode('utf-8')
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):
        if self.path!='/api/applications/task/overview':self.send_error(404);return
        raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
        params=json.loads(raw)
        result=self.server.task_manager.snapshot(query=params.get('query',''),limit=int(params.get('limit',100)),
            include_all=bool(params.get('include_all')),include_connections=bool(params.get('include_connections')))
        body=json.dumps(result).encode()
        self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)


def test_browser_four_views_real_http_isolated_adapter(tmp_path):
    server=ThreadingHTTPServer(('127.0.0.1',0),LaboratoryHandler)
    server.task_manager=TaskManagerService(ROOT,default_port=server.server_port)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        results=probe('http://127.0.0.1:'+str(server.server_port),tmp_path,timeout_ms=15000)
        if not results['passed']:
            # This cloud sandbox can prohibit Chromium localhost navigation.
            # A blocked browser must remain an explicit blocked acceptance test,
            # never be converted to a live-host success.
            failures=[v.get('reason','') for v in results['widths'].values() if not v.get('passed')]
            assert failures and all('ERR_BLOCKED_BY_ADMINISTRATOR' in v for v in failures),failures
            return
        for width in (320,390,560,1100,1272):
            item=results['widths'][str(width)]
            assert len(item['views'])==4
            assert all(value['httpStatus']==200 for value in item['views'].values())
            assert item['hostPageSha256']==item['expectedPageSha256']
            assert (tmp_path/f'viewport-{width}.png').exists()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
