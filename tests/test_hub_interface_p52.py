"""P5.2: shared Worker/requester network resolution; no production mutations."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from main_computer.hub_interface import (
    HubInterfaceError,
    require_legacy_overflow,
    resolve_hub_destination,
    selected_worker_network,
)
from main_computer.remote_overflow import RemoteHubExecutionGateway, RemoteOverflowAssessment
from main_computer.models import ChatResponse
from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin
from main_computer.viewport_routes_chat_console import ViewportChatConsoleRoutesMixin


@pytest.mark.parametrize("network,kind,protocol,url", [
    ("dev", "dev-test-hub", "dev-test", "http://127.0.0.1:8871"),
    ("test", "dev-test-hub", "dev-test", "http://127.0.0.1:8780"),
    ("testnet", "stable-hub", "stable-hub-paid", "https://testnet-hub.greatlibrary.io"),
    ("mainnet", "stable-hub", "stable-hub-paid", "https://mainnet-hub.greatlibrary.io"),
])
def test_selected_worker_network_resolves_registry_without_mainnet_default(network,kind,protocol,url):
    target = resolve_hub_destination({"selectedNetwork": network, "registrationHubUrl": "http://127.0.0.1:8871"})
    assert (target.network,target.hub_kind,target.protocol,target.hub_url) == (network,kind,protocol,url)


def test_absent_selection_is_off_not_implicit_public_registry_default():
    assert resolve_hub_destination({}).diagnostics() == {
        "selected_network": "none", "hub_url": "", "hub_kind": "none", "protocol": "none",
        "legacy_overflow_supported": False,
    }
    with pytest.raises(HubInterfaceError):
        require_legacy_overflow(resolve_hub_destination({}))


def test_supervisor_auto_selection_wins_same_way_worker_sanitizer_does():
    assert selected_worker_network({"selectedNetwork": "mainnet", "workerAutoConnectNetwork": "dev"}) == "dev"
    assert selected_worker_network({"selectedNetwork": "test", "workerAutoConnectNetwork": "none"}) == "test"
    assert selected_worker_network({"selectedNetwork": "slh"}) == "slh"
    with pytest.raises(HubInterfaceError):
        resolve_hub_destination({"selectedNetwork": "unsupported"})


@pytest.mark.parametrize("network", ["mainnet", "testnet", "none", "slh"])
def test_legacy_simulation_refuses_non_dev_selected_network_without_fallback(network):
    with pytest.raises(HubInterfaceError, match="No Hub fallback"):
        require_legacy_overflow(resolve_hub_destination({"selectedNetwork": network}))


def test_gateway_ignores_untrusted_hub_payload_and_config_when_bound_to_worker_selection():
    calls = []
    class SpyProvider:
        def __init__(self, **kw):
            calls.append(kw)
    destination = resolve_hub_destination({"selectedNetwork": "test"})
    gateway = RemoteHubExecutionGateway(destination=destination,provider_factory=SpyProvider)
    gateway._provider_for_request({"hub_url":"http://127.0.0.1:8871", "hub":{"url":"https://mainnet-hub.greatlibrary.io"}})
    assert calls[0]["hub_url"] == "http://127.0.0.1:8780"


def test_gateway_selected_public_network_never_constructs_dev_provider():
    calls=[]
    gateway=RemoteHubExecutionGateway(
        destination=resolve_hub_destination({"selectedNetwork":"mainnet"}),
        provider_factory=lambda **kw: calls.append(kw),
    )
    with pytest.raises(HubInterfaceError,match="does not execute real requester work"):
        gateway._provider_for_request({"hub":{"url":"http://127.0.0.1:8871"}})
    assert calls == []


class FakeViewport(ViewportChatConsoleRoutesMixin,ViewportEnergyRoutesMixin):
    def __init__(self, root):
        self.server=SimpleNamespace(debug_root=root, config=SimpleNamespace(
            hub_url="http://127.0.0.1:8871",hub_network="dev", model="sample",
            hub_timeout_s=1,hub_client_node_id="test-client",hub_high_security=False,
            hub_allow_insecure_dev_network=False),
        )


def test_worker_page_and_both_chat_console_variants_share_one_settings_record(tmp_path):
    view=FakeViewport(tmp_path)
    initial=view._save_worker_settings({"settings":{"selectedNetwork":"dev","workerAutoConnectNetwork":"dev","remoteEnabled":False}})
    assert resolve_hub_destination(view._chat_console_load_worker_settings()).network == "dev"
    # Both Chat Console variants use the same settings API with changed_fields.
    changed=view._save_worker_settings({"settings":{"remoteEnabled":True}},changed_fields=["remoteEnabled"])
    assert changed["remoteEnabled"] is True
    assert changed["selectedNetwork"] == "dev"
    view._save_worker_settings({"settings":{"selectedNetwork":"test","workerAutoConnectNetwork":"test"}},
                               changed_fields=["selectedNetwork","workerAutoConnectNetwork"])
    assert resolve_hub_destination(view._chat_console_load_worker_settings()).hub_url == "http://127.0.0.1:8780"
    assert view._load_worker_settings()["remoteEnabled"] is True


def test_chat_overflow_backend_does_not_use_stale_registration_or_other_network_msk(tmp_path):
    view=FakeViewport(tmp_path)
    view._save_worker_settings({"settings":{"selectedNetwork":"test", "workerAutoConnectNetwork":"test",
                                            "registrationHubUrl":"http://127.0.0.1:8871", "remoteEnabled":True}})
    cache={"keys":{
      "old":{"id":"msk-old", "wallet_address":"0x"+"1"*40,"hub_url":"http://127.0.0.1:8871","status":"active"}
    }}
    view._chat_console_worker_multisession_cache=lambda:cache
    context=view._chat_console_backend_paid_overflow_context({"messages":[{"role":"user","content":"hello"}]})
    assert context["hub_url"] == "http://127.0.0.1:8780"
    assert context["multisession_key_id"] == ""
    assert context["hub_destination"].network == "test"
    enriched,_=view._chat_console_enrich_remote_overflow_body_with_backend_context({"hub_url":"https://mainnet-hub.greatlibrary.io","hub":{"url":"http://127.0.0.1:8871"}})
    assert enriched["hub_url"] == "http://127.0.0.1:8780"
    assert enriched["hub"]["url"] == "http://127.0.0.1:8780"


def test_worker_network_page_and_requester_target_share_resolver(tmp_path):
    view=FakeViewport(tmp_path)
    for network in ("dev","test","mainnet","testnet"):
        url=view._worker_network_profile_hub_url(network)
        assert url == resolve_hub_destination({"selectedNetwork":network}).hub_url


def test_selection_change_requires_no_new_hub_or_supervisor(tmp_path):
    view=FakeViewport(tmp_path)
    view._save_worker_settings({"settings":{"selectedNetwork":"mainnet","workerAutoConnectNetwork":"mainnet"}})
    before=view._chat_console_load_worker_settings()
    after=view._chat_console_load_worker_settings()
    assert before == after
    assert resolve_hub_destination(after).network == "mainnet"


def test_simulated_result_cannot_be_misreported_as_real_paid_worker():
    class FakeProvider:
        def __init__(self, **kw):
            pass
        def remote_overflow_safe_chat(self, messages, *, remote_overflow_request_id, metadata=None):
            return ChatResponse(content="development preview",provider="remote-hub-ai",model="fake-model",metadata={})
    target=resolve_hub_destination({"selectedNetwork":"dev"})
    gateway=RemoteHubExecutionGateway(destination=target,provider_factory=FakeProvider)
    assessment=RemoteOverflowAssessment(status="ready",action="remote_offer",reason_code="ready",
        user_message="ready",authorization_required=True,offer_remote=True,cards=[])
    result=gateway.run({"messages":[{"role":"user","content":"hello"}],"remote_overflow_enabled":True},assessment)
    assert result["simulated"] is True
    assert result["remote_overflow_result"]["simulated"] is True
    assert result["remote_overflow_result"]["response"]["metadata"]["simulated"] is True
    assert result["remote_overflow_result"]["response"]["metadata"]["no_real_paid_worker_contacted"] is True
    assert result["routing"]["selected_network"] == "dev"


def test_public_selection_reports_unsupported_without_contacting_dev_hub(tmp_path):
    view=FakeViewport(tmp_path)
    view._save_worker_settings({"settings":{"selectedNetwork":"mainnet","workerAutoConnectNetwork":"mainnet","remoteEnabled":True}})
    readiness=view._chat_console_backend_hub_readiness({"messages":[{"role":"user","content":"hello"}]})
    assert readiness["ready"] is False
    assert readiness["reason_code"] == "unsupported_selected_hub_protocol"
    assert readiness["routing"]["selected_network"] == "mainnet"
    assert readiness["routing"]["protocol"] == "stable-hub-paid"


def test_readonly_profile_verifier_and_no_nonloopback_live_probe():
    from tools.hub_interface_verify import verify_profiles, verify_live_viewport
    report=verify_profiles()
    assert report["ok"] is True
    assert [route["selected_network"] for route in report["routes"]] == ["dev","test","testnet","mainnet","none"]
    with pytest.raises(ValueError,match="local-only"):
        verify_live_viewport("http://192.0.2.1:8765")


def test_live_verifier_does_not_print_sensitive_worker_state(monkeypatch):
    from tools import hub_interface_verify
    import json
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, limit):
            return json.dumps({"ok":True,"settings":{"selectedNetwork":"test","remoteEnabled":True,
                        "signedWorkerConnection":{"signature":"confidential","message":"secret"}}}).encode()
    monkeypatch.setattr(hub_interface_verify,"urlopen",lambda request,timeout:Response())
    report=hub_interface_verify.verify_live_viewport()
    assert report["routing"]["selected_network"] == "test"
    assert "confidential" not in json.dumps(report)
    assert "secret" not in json.dumps(report)


def test_public_chat_submit_explicitly_unsupported_without_invoking_any_hub(tmp_path,monkeypatch):
    view=FakeViewport(tmp_path)
    view._save_worker_settings({"settings":{"selectedNetwork":"mainnet","workerAutoConnectNetwork":"mainnet"}})
    view._read_json=lambda:{"messages":[{"role":"user","content":"test"}]}
    view._chat_console_remote_hub_submit_body=lambda body:body
    assessment=RemoteOverflowAssessment(status="ready", action="remote_offer", reason_code="ready",
        user_message="ready",authorization_required=True,offer_remote=True,cards=[])
    view._chat_console_remote_hub_submit_engine=lambda body:SimpleNamespace(assess=lambda b:assessment)
    sent=[]
    view._send_json=lambda result,**kw:sent.append((result,kw))
    view.server.signal=lambda *args,**kw:None
    monkeypatch.setattr(RemoteHubExecutionGateway,"run",lambda *a,**k:pytest.fail("Gateway contacted"))
    view._handle_chat_console_remote_overflow_hub_submit()
    assert len(sent)==1
    assert sent[0][0]["status"] == "unsupported"
    assert sent[0][0]["reason_code"] == "unsupported_selected_hub_protocol"
    assert sent[0][0]["routing"]["selected_network"] == "mainnet"
