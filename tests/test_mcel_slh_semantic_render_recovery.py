"""Regression: a bounded semantic form label must never break the entire SLH GET.

The HTTP fixture exercises the real viewport GET dispatch, not a replacement
route. It has no gateway, Organization Service, or first-run side effects.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_slh_application import (
    SOURCE, ROOT, SlhApplicationError, compile_slh_application, render_slh_page,
)
from main_computer.viewport_http import ViewportHttpMixin
from main_computer.viewport_route_dispatch import dispatch_get


@contextmanager
def _slh_get_server():
    signals = []

    class Handler(ViewportHttpMixin, BaseHTTPRequestHandler):
        def do_GET(self):
            dispatch_get(self)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.signal = lambda name, **fields: signals.append((name, fields))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", signals
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_slh_semantic_form_label_compiles_and_page_renders_without_lifecycle_calls():
    ir, _ = compile_slh_application()
    forms = {n["id"]: n for n in ir["surfaces"][0]["nodes"] if n["nodeKind"] == "form"}
    assert forms["surface-node:slh.configure-org-slh"]["submitLabel"] == "Configure ORG-SLH"
    for view in ("overview", "network"):
        html = render_slh_page(view)
        assert "mcel-generated-data" in html
        assert 'Configure ORG-SLH' in html
        assert 'data-mcel-slh-target="' + ("network" if view == "network" else "local") + '"' in html


def test_slh_http_get_returns_complete_html_instead_of_empty_response():
    with _slh_get_server() as (url, signals):
        for target in ("local", "network"):
            with urlopen(f"{url}/applications/slh/{target}", timeout=10) as response:
                data = response.read()
                assert response.status == 200
                assert response.headers["Content-Type"] == "text/html; charset=utf-8"
                assert len(data) > 100_000
                assert b'mcel-generated-data' in data
        assert not any(name == "mcel-slh-render-failed" for name, _ in signals)


def test_invalid_form_submit_label_is_rejected_by_strict_dsl(tmp_path: Path):
    original = (ROOT / SOURCE).read_text(encoding="utf-8")
    for substitute in ('submitLabel:""', 'submitLabel:42'):
        source = tmp_path / "application.js"
        source.write_text(original.replace('submitLabel:"Configure ORG-SLH"', substitute), encoding="utf-8")
        report = compile_dsl_application(source)
        assert not report.valid
        assert any(d["code"] == "MCEL_DSL_FORM_SUBMIT_LABEL_INVALID" for d in report.diagnostics)


def test_invalid_semantic_source_returns_503_not_connection_abort(monkeypatch):
    import main_computer.mcel_slh_application as host

    def render_error(*args, **kwargs):
        raise SlhApplicationError("invalid test declaration")

    monkeypatch.setattr(host, "render_slh_page", render_error)
    with _slh_get_server() as (url, signals):
        try:
            urlopen(f"{url}/applications/slh/local", timeout=10)
        except HTTPError as error:
            assert error.code == 503
            assert error.read()
        else:
            raise AssertionError("Expected explicit HTTP 503 for invalid semantic source")
        assert any(name == "mcel-slh-render-failed" for name, _ in signals)
