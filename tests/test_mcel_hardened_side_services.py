"""Candidate-only probes for services outside the Viewport ingress router."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from main_computer.main_log_service import MainLogHTTPServer, MainLogRequestHandler, MainLogStore
from main_computer.openclaw_bridge import OpenClawBridgeHandler
from main_computer.heartbeat import HeartbeatHandler


class SideServiceTests(unittest.TestCase):
    def test_main_log_denies_anonymous_reader_and_writer_but_allows_token(self):
        with tempfile.TemporaryDirectory(prefix='mc-log-auth-') as root:
            with patch.dict(os.environ, {'MAIN_COMPUTER_HARDENED_CANDIDATE':'1',
                                         'MAIN_COMPUTER_MAIN_LOG_TOKEN':'Q'*48}):
                with MainLogHTTPServer(('127.0.0.1',0),MainLogRequestHandler,MainLogStore(root=root)) as server:
                    thread=threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        def request(method,path,body=None,token=None):
                            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                            hdr={'Content-Type':'application/json'}
                            if token: hdr['Authorization']='Bearer '+token
                            try:
                                connection.request(method,path,body=body,headers=hdr)
                                response=connection.getresponse()
                                return response.status,json.loads(response.read())
                            finally:connection.close()
                        status,body=request('GET','/health')
                        self.assertEqual(status,200)
                        self.assertNotIn('root',body)
                        self.assertEqual(request('GET','/v1/log/recent')[0],401)
                        self.assertEqual(request('POST','/v1/log/events',body=b'{"events":[]}')[0],401)
                        self.assertEqual(request('GET','/v1/log/recent',token='Q'*48)[0],200)
                    finally:
                        server.shutdown();thread.join(timeout=4)

    def test_heartbeat_refuses_anonymous_authority_session(self):
        handler=HeartbeatHandler.__new__(HeartbeatHandler)
        handler.path='/api/heartbeat/authority/session'
        seen=[]
        handler._send_json=lambda body, status=None: seen.append((int(status),body))
        with patch.dict(os.environ, {'MAIN_COMPUTER_HARDENED_CANDIDATE':'1'}):
            handler.do_GET()
        self.assertEqual(seen[0][0],401)

    def test_openclaw_no_token_fails_closed(self):
        handler=OpenClawBridgeHandler.__new__(OpenClawBridgeHandler)
        handler.server=SimpleNamespace(token=None,signal=lambda *a,**kw:None)
        handler.headers={'Authorization':''}
        handler.path='/v1/projects'
        handler.client_address=('127.0.0.1',0)
        seen=[]
        handler._send_json=lambda body, status=None:seen.append((int(status),body))
        with patch.dict(os.environ, {'MAIN_COMPUTER_HARDENED_CANDIDATE':'1'}):
            self.assertFalse(handler._authorize())
        self.assertEqual(seen[0][0],401)

if __name__=='__main__':unittest.main()
