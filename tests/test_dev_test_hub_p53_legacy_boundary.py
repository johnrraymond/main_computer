"""P5.3: Dev-Test Hub's explicit inherited-route contract (no live Hub needed)."""
from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace

import pytest

from main_computer.dev_test_hub_legacy_routes import inherited_legacy_hub_route
from main_computer.exp_fdb_hub import ExperimentalFoundationDbHubServerHandler
from main_computer.hub import HubServerHandler
from main_computer.hub_admin_site import HUB_ADMIN_ROUTES


@pytest.mark.parametrize("path", [
    "/api/hub/status", "/api/hub/v1/status", "/api/hub/v1/health",
    "/api/hub/v1/workers", "/api/hub/v1/models", "/api/hub/v1/credits",
    "/api/hub/v1/credits/charges", "/api/hub/v1/credits/holds",
    "/api/hub/v1/requests", "/api/hub/v1/requests/test-result",
    "/api/hub/v1/workers/sensitive", "/api/hub/v1/bridge/audit",
    "/api/hub/v1/admin/bootstrap", "/api/hub/v1/metrics",
    "/api/hub/v1/ring-control/feedback-summary", "/api/hub/payouts",
    "/api/hub/v1/payouts", *sorted(HUB_ADMIN_ROUTES),
])
def test_existing_inherited_get_surface_explicit(path):
    assert inherited_legacy_hub_route("GET", path)


@pytest.mark.parametrize("path", [
    "/api/hub/remote-overflow/safe-chat",
    "/api/hub/v1/credits/multisession-keys/request",
    "/api/hub/v1/credits/multisession-keys/revoke",
    "/api/hub/v1/credits/multisession-keys/validate",
    "/api/hub/workers/connect", "/api/hub/v1/workers/connect",
    "/api/hub/workers/stream-events", "/api/hub/v1/workers/stream-events",
    "/api/hub/sessions/start", "/api/hub/sessions/chat",
    "/api/hub/v1/credits/wallet-funding/complete",
    "/api/hub/v1/requests/quote", "/api/hub/v1/requests",
    "/api/hub/v1/requests/req-1/feedback", "/api/hub/chat",
])
def test_existing_inherited_post_surface_explicit(path):
    assert inherited_legacy_hub_route("POST", path)


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/hub/future-broker"),
    ("POST", "/api/hub/future-broker"),
    ("POST", "/api/hub/remote-overflow/new-bypass"),
    ("POST", "/api/hub/v1/credits/deposits/import"),
    ("POST", "/api/hub/v1/credits/claims"),
    ("POST", "/api/hub/v1/workers/register"),
    ("POST", "/api/hub/workers/register"),
    ("POST", "/api/hub/v1/workers/results"),
    ("POST", "/api/hub/v1/workers/heartbeat"),
    ("POST", "/api/hub/v1/requests/fake/result"),
    ("POST", "/api/hub/v1/requests/fake/deep/feedback"),
    ("GET", "/api/hub/chat"),
    ("PATCH", "/api/hub/v1/requests"),
])
def test_undeclared_or_deprecated_routes_not_inherited(method,path):
    assert not inherited_legacy_hub_route(method,path)


def bare_dev_test_handler(path):
    handler = object.__new__(ExperimentalFoundationDbHubServerHandler)
    handler.path = path
    handler.server = SimpleNamespace(stable_hub_node=SimpleNamespace(hub_id="dev-hub1"))
    return handler


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/hub/future-broker?foo=bar"),
    ("POST", "/api/hub/future-broker?foo=bar"),
    ("POST", "/api/hub/v1/credits/deposits/import"),
])
def test_fdb_handler_rejects_undeclared_inherited_routes_before_base(monkeypatch,method,path):
    inherited = []
    monkeypatch.setattr(HubServerHandler, "do_GET", lambda h: inherited.append("GET"))
    monkeypatch.setattr(HubServerHandler, "do_POST", lambda h: inherited.append("POST"))
    handler = bare_dev_test_handler(path)
    responses = []
    handler.send_error = lambda status:responses.append(status)
    getattr(handler,"do_"+method)()
    assert inherited == []
    assert responses == [HTTPStatus.NOT_FOUND]


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/hub/status?debug=true"),
    ("GET", "/api/hub/v1/credits/charges"),
    ("GET", "/admin"),
    ("POST", "/api/hub/sessions/start"),
    ("POST", "/api/hub/remote-overflow/safe-chat"),
    ("POST", "/api/hub/v1/requests/req-1/feedback"),
])
def test_fdb_handler_delegates_only_declared_legacy_routes(monkeypatch,method,path):
    inherited = []
    monkeypatch.setattr(HubServerHandler, "do_GET", lambda h: inherited.append("GET"))
    monkeypatch.setattr(HubServerHandler, "do_POST", lambda h: inherited.append("POST"))
    handler = bare_dev_test_handler(path)
    handler.send_error = lambda status: pytest.fail(f"unexpected {status}")
    getattr(handler,"do_"+method)()
    assert inherited == [method]


def test_removed_legacy_worker_post_still_has_explicit_404_before_base(monkeypatch):
    monkeypatch.setattr(HubServerHandler, "do_POST", lambda h:pytest.fail("deprecated route inherited"))
    handler=bare_dev_test_handler("/api/hub/v1/workers/register")
    replies=[]
    handler._send_json=lambda payload,**kwargs: replies.append((payload,kwargs))
    handler.do_POST()
    assert replies[0][0]["error"] == "not_found"
    assert replies[0][1]["status"] == HTTPStatus.NOT_FOUND


def test_real_http_unknown_legacy_routes_404_and_existing_status_unchanged():
    """Exercise the actual FDB handler's HTTP dispatch, not just route predicates."""
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    server = ThreadingHTTPServer(("127.0.0.1", 0), ExperimentalFoundationDbHubServerHandler)
    server.verbose = False
    # Minimal bounded-handler prerequisites; no FoundationDB is needed.
    server.resource_begin_headers = lambda connection: True
    server.resource_headers_complete = lambda connection: True
    server.stable_hub_node = SimpleNamespace(hub_id="dev-hub1")
    server.config = SimpleNamespace(hub_network="dev", chain_id=42424242)
    server.registry = SimpleNamespace(status=lambda: {
        "worker_count": 2, "available_worker_count": 1, "stale_worker_count": 0,
    })
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_port}"
        with urlopen(root + "/api/hub/status", timeout=3) as response:
            status = json.load(response)
        assert status["network_key"] == "dev"
        assert status["worker_count"] == 2
        for req in (
            Request(root + "/api/hub/future-broker",method="GET"),
            Request(root + "/api/hub/future-broker",data=b"{}",method="POST"),
            Request(root + "/api/hub/future-broker",data=b"not-json",method="POST"),
            Request(root + "/api/hub/v1/credits/deposits/import",data=b"{}",method="POST"),
        ):
            with pytest.raises(HTTPError) as error:
                urlopen(req,timeout=3)
            assert error.value.code == 404
        with pytest.raises(HTTPError) as private_read:
            urlopen(root + "/api/hub/v1/credits/charges",timeout=3)
        assert private_read.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_p53_readonly_verifier_proves_declared_contract():
    from tools.dev_test_hub_inheritance_verify import verify_contract
    report = verify_contract()
    assert report["ok"] is True
    assert all(report["checks"].values())


@pytest.mark.parametrize("body", [b"{}", b"not-json"])
def test_unknown_post_discard_small_body_without_parsing_or_redirecting(body):
    """A Windows 404 must be readable despite a caller-supplied request body."""
    from email.message import Message
    from io import BytesIO

    handler = bare_dev_test_handler("/api/hub/undeclared-route")
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(body))
    handler.rfile = BytesIO(body)
    class Connection:
        def gettimeout(self):
            return None
        def settimeout(self, value):
            pass
    handler.connection = Connection()
    errors = []
    handler.send_error = lambda status: errors.append(status)
    handler.do_POST()
    assert errors == [HTTPStatus.NOT_FOUND]
    assert handler.rfile.tell() == len(body)


def test_unknown_post_does_not_read_oversized_body():
    """Rejecting unknown routes must not read an arbitrary request payload."""
    from email.message import Message
    from io import BytesIO

    handler = bare_dev_test_handler("/api/hub/undeclared-route")
    handler.headers = Message()
    handler.headers["Content-Length"] = "999999"
    handler.rfile = BytesIO(b"UNTRUSTED")
    errors = []
    handler.send_error = lambda status: errors.append(status)
    handler.do_POST()
    assert errors == [HTTPStatus.NOT_FOUND]
    assert handler.rfile.tell() == 0
    assert handler.close_connection is True
