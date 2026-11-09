"""P5.3d: exact shared HTTP handler extraction; no live Hub or payment."""
from __future__ import annotations

import http.client
import json
import threading
from http import HTTPStatus
from http.server import ThreadingHTTPServer

import pytest

from main_computer.hub import _JsonHandler, HubHttpServer, HubServerHandler, HubWorkerHandler
from main_computer.hub_http_shared import HubJSONRequestHandler
from main_computer.hub_resource_limits import HubResourcePolicy
from tools.hub_http_shared_verify import verify_contract


def test_shared_http_extraction_contract_and_exact_body():
    result=verify_contract()
    assert result['ok'] is True, result
    assert HubJSONRequestHandler is _JsonHandler
    assert HubServerHandler.__bases__ == (HubJSONRequestHandler,)
    assert HubWorkerHandler.__bases__ == (HubJSONRequestHandler,)
    assert HubHttpServer.__module__ == 'main_computer.hub_http_runtime'


class _Echo(HubJSONRequestHandler):
    """Minimal real socket probe of the unchanged JSON body framing."""

    def do_GET(self):
        self._send_json({'ok': True, 'path': self.path})

    def do_POST(self):
        if not self._preflight_post():
            return
        self._send_json({'ok': True, 'payload': self._read_json()})


@pytest.fixture
def shared_handler_http_server():
    server=ThreadingHTTPServer(('127.0.0.1',0), _Echo)
    server.verbose=False
    server.resource_limits=HubResourcePolicy(http_body_max_bytes=16)
    server.resource_begin_headers=lambda connection: True
    server.resource_headers_complete=lambda connection: True
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(port,method,path,body=None,headers=None):
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
    try:
        connection.request(method,path,body=body,headers=headers or {})
        reply=connection.getresponse()
        return reply.status,json.loads(reply.read())
    finally:
        connection.close()


def test_shared_http_get_and_valid_bounded_json_post(shared_handler_http_server):
    port=shared_handler_http_server.server_port
    status,body=_request(port,'GET','/check')
    assert status==200 and body == {'ok':True,'path':'/check'}
    status,body=_request(port,'POST','/check',body=b'{"x":1}',headers={'Content-Type':'application/json'})
    assert status==200 and body == {'ok':True,'payload':{'x':1}}


def test_shared_http_rejects_invalid_json_without_dispatch(shared_handler_http_server):
    status,body=_request(shared_handler_http_server.server_port,'POST','/check',body=b'{invalid}',headers={'Content-Type':'application/json'})
    assert status==HTTPStatus.BAD_REQUEST
    assert body['ok'] is False and body['error']=='invalid_json_body'


def test_shared_http_rejects_declared_oversize_before_reading_body(shared_handler_http_server):
    # Send ONLY the headers to avoid unread payload resetting Windows sockets.
    client=http.client.HTTPConnection('127.0.0.1',shared_handler_http_server.server_port,timeout=3)
    try:
        client.putrequest('POST','/check')
        client.putheader('Content-Length','1024')
        client.endheaders()
        response=client.getresponse()
        assert response.status==HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        body=json.loads(response.read())
        assert body['error']=='http_body_too_large'
    finally:
        client.close()
