"""Candidate SLH semantic application: user workflows without UI preauthoring.

No SLH is started; all observations are bounded, read-only and explicit about
unauthenticated Worker Bank state. This is NOT final SLH oracle acceptance.
"""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

import pytest

from main_computer.config import MainComputerConfig
from main_computer.mcel_slh_application import (
    ROOT, SCHEMA, SlhApplicationError, compile_slh_application,
    observe_slh, render_slh_page,
)


def _fake_status():
    return ("RUNNING", {
        "schema": "main-computer.slh-local-status.v1",
        "local_service": {"state": "running", "binding": "loopback"},
        "initialization": {"initialized": True, "setup_phase": "INITIALIZED"},
        "organization_service": {"state": "disabled"},
        "ignored_private_key": "NEVER_RELAY_THIS_VALUE",
    })


@pytest.fixture(scope="module")
def host():
    # The snapshot omits optional game_projects assets; do not make this
    # unrelated demo manifest a dependency of the SLH route tests.
    with patch("main_computer.game_web_loader.read_game_bundle", return_value="<div></div>"):
        from main_computer.viewport_server import ViewportServer
    server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=ROOT), verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_semantic_source_is_authority_not_html_or_compatibility_scaffold():
    ir, plan = compile_slh_application()
    assert ir["application"]["appId"] == "slh"
    assert not ir["layouts"]
    assert plan["schema"] == "mcel.generated-composition.v1"
    assert plan["policy"] == "mcel.semantic-structure.p3"
    assert sorted(e["risk"] for e in ir["effects"]) == ["consequential"] * 7 + ["external-read"] * 7
    assert {i["id"] for i in ir["intents"]} == {
        "intent:refresh-overview", "intent:refresh-health", "intent:refresh-workers",
        "intent:refresh-worker-summary", "intent:select-view", "intent:choose-worker", "intent:rename-worker",
        "intent:start-local", "intent:bootstrap-administrator", "intent:configure-organization",
        "intent:activate-organization", "intent:login-administrator", "intent:logout-administrator",
        "intent:refresh-identities", "intent:refresh-identity-status", "intent:choose-identity",
        "intent:select-target", "intent:inspect-network-slh"
    }
    assert '"worker-bank":"/applications/slh/local/workers"' in render_slh_page()
    assert 'overview:"/applications/slh/local"' in render_slh_page("worker-bank")
    assert "mcel.generated-composition.v1" in render_slh_page()
    assert 'data-mcel-slh-initial-view="worker-bank"' in render_slh_page("worker-bank")
    assert "mcel-workbench" in render_slh_page()


def test_truthful_backend_projection_never_invents_worker_inventory():
    overview = observe_slh("overview", probe=_fake_status)
    workers = observe_slh("workers", probe=_fake_status)
    assert overview["schema"] == SCHEMA and overview["view"] == "overview"
    assert workers["view"] == "workers"
    assert overview["overview"]["initializationState"] == "INITIALIZED"
    assert overview["overview"]["organizationState"] == "disabled"
    assert workers["workerSummary"]["observationState"] == "AUTH_REQUIRED"
    assert workers["workers"] == []
    assert "NEVER_RELAY_THIS_VALUE" not in json.dumps(overview)


@pytest.mark.parametrize("state,expected", [("UNREACHABLE", "NOT_OBSERVED"), ("UNVERIFIED", "NOT_OBSERVED")])
def test_nonoperational_service_is_not_zero_workers(state, expected):
    data = observe_slh("workers", probe=lambda: (state, {}))
    assert data["overview"]["serviceState"] == state
    assert data["workerSummary"]["observationState"] == expected
    assert "zero" in data["workerSummary"]["explanation"]


def test_refuses_undeclared_views_and_unvalidated_local_observation():
    for view in ("worker-bank", "admin", "", "../../status"):
        with pytest.raises(SlhApplicationError):
            observe_slh(view, probe=_fake_status)
    with pytest.raises(SlhApplicationError):
        render_slh_page("admin")
    with pytest.raises(SlhApplicationError):
        observe_slh("overview", probe=lambda: ("not-a-service", {}))


def test_native_routes_and_fixed_read_api_are_reachable(host):
    for route, expected in (("/applications/slh", "overview"),
                            ("/applications/slh/workers", "worker-bank")):
        with urlopen(host + route, timeout=20) as response:
            document = response.read().decode()
        assert f'data-mcel-slh-initial-view="{expected}"' in document
        assert '"worker-bank":"/applications/slh/local/workers"' in document
        assert 'mcel-generated-data' in document
    with patch("main_computer.mcel_slh_application.observe_slh", side_effect=lambda view, **kw: observe_slh(view, probe=_fake_status, **kw)):
        for view in ("overview", "workers"):
            with urlopen(host + "/api/applications/slh/observe?view=" + view, timeout=20) as response:
                data = json.loads(response.read())
            assert data["view"] == view and data["schema"] == SCHEMA
        with pytest.raises(HTTPError) as exc:
            urlopen(host + "/api/applications/slh/observe?view=admin", timeout=20)
        assert exc.value.code == 400


def test_remote_cannot_open_slh_or_its_observation_api(host):
    with patch("main_computer.viewport_route_dispatch._hard_halt_client_is_local", return_value=False):
        for path in ("/applications/slh", "/applications/slh/workers",
                     "/api/applications/slh/observe?view=overview"):
            with pytest.raises(HTTPError) as exc:
                urlopen(host + path, timeout=20)
            assert exc.value.code == 403


@pytest.mark.parametrize("view", ["overview", "worker-bank"])
def test_browser_local_configuration_route_reads_only_local_identities(view):
    # Both canonical deep-links now mount the same configuration-only workspace.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1040,'height':800})
            errors=[]
            page.on('pageerror',lambda error: errors.append(str(error)))
            page.evaluate("""()=>{
                window.calls=[];
                window.fetch=async(url,opts={})=>{
                    window.calls.push({url,method:opts.method});
                    if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
                        schema:'mcel.slh.identities.v4b',state:'UNAVAILABLE',defaultDeviceId:'',
                        identities:[],explanation:'Identity service unavailable'})};
                    throw Error('Unexpected lifecycle or observation call: '+url);
                };
            }""")
            page.set_content(render_slh_page(view))
            page.get_by_text('ORG-SLH configuration draft',exact=False).wait_for(timeout=15000)
            assert page.locator('form[data-mcel-draft-only]').count()==1
            assert page.locator('button[data-mcel-draft-submit]').is_disabled()
            assert not page.locator('button[data-intent="intent:login-administrator"]').count()
            assert page.evaluate('window.calls')==[
                {'url':'/api/applications/slh/identities','method':'GET'},
                {'url':'/api/applications/slh/identities','method':'GET'}]
            assert errors==[],errors
        finally:browser.close()


def test_worker_deep_link_does_not_enter_organization_administration():
    # An obsolete Worker Bank URL is not permission to observe inventory,
    # bootstrap an organization, or expose administrator login.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page()
            page.evaluate("""()=>{window.calls=[];window.fetch=async(url,opts={})=>{
                window.calls.push(url);
                if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
                    schema:'mcel.slh.identities.v4b',state:'UNAVAILABLE',defaultDeviceId:'',
                    identities:[],explanation:'No identities'})};
                throw Error('No authorization for '+url);
            }}""")
            page.set_content(render_slh_page('worker-bank'))
            page.get_by_text('ORG-SLH configuration draft',exact=False).wait_for(timeout=15000)
            assert not page.locator('button[data-intent="intent:login-administrator"]').count()
            assert all(x=='/api/applications/slh/identities' for x in page.evaluate('window.calls'))
        finally:browser.close()
