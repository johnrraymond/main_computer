"""P5.4d: prevent implicit Worker REST fallback; no network or paid work."""
from __future__ import annotations

from dataclasses import replace

import pytest

from main_computer import hub, hub_worker_client as client
from main_computer.config import MainComputerConfig
from main_computer.models import ChatResponse
from tools.hub_worker_client_verify import verify_contract as verify_client
from tools.hub_worker_http_verify import verify_contract as verify_http
from tools.hub_worker_retirement_decision import verify as verify_retirement


def _config(provider="ollama"):
    return replace(MainComputerConfig.from_env(), provider=provider, model="demo-model",
                   hub_url="https://unknown.example", hub_worker_node_id="unit-worker",
                   hub_worker_endpoint="https://worker.example")


def _chat(messages):
    return ChatResponse(content="done", provider="unit-test", model="demo-model")


def test_unrecognized_hub_identity_refuses_before_rest_registration(monkeypatch):
    from main_computer import hub_cli_live_session as live
    monkeypatch.setattr(live, "identify_hub_protocol", lambda *a, **kw: {"service":"unrecognized_service"})
    monkeypatch.setattr(client, "register_worker_with_hub", lambda **kw: pytest.fail("no REST registration"))
    monkeypatch.setattr(client, "_post_hub_worker_json", lambda **kw: pytest.fail("no REST polling"))
    with pytest.raises(RuntimeError, match="Refusing implicit legacy REST"):
        hub.serve_hub_worker_pull(_config(), _chat, verbose=False)


def test_intentional_rest_opt_in_retains_existing_transport(monkeypatch):
    from main_computer import hub_cli_live_session as live
    monkeypatch.setattr(live, "identify_hub_protocol", lambda *a, **kw: {"service":"unrecognized_service"})
    observed = []
    class ExpectedStop(Exception):
        pass
    def register(**kw):
        observed.append(kw)
        raise ExpectedStop()
    monkeypatch.setattr(client, "register_worker_with_hub", register)
    with pytest.raises(ExpectedStop):
        hub.serve_hub_worker_pull(_config(), _chat, verbose=False, allow_legacy_worker_rest=True)
    assert len(observed) == 1
    assert observed[0]["hub_url"] == "https://unknown.example"
    assert observed[0]["capabilities"]["worker_pull_v0"] is True


@pytest.mark.parametrize("service", ["main_computer.exp_fdb_hub", "main_computer.stable_hub"])
@pytest.mark.parametrize("explicit", [False, True])
def test_recognized_hubs_always_use_live_session_even_with_rest_opt_in(monkeypatch, service, explicit):
    from main_computer import hub_cli_live_session as live
    monkeypatch.setattr(live, "identify_hub_protocol", lambda *a, **kw: {"service":service})
    seen = []
    monkeypatch.setattr(live, "serve_cli_live_worker", lambda **kw:seen.append(kw))
    monkeypatch.setattr(client, "register_worker_with_hub", lambda **kw: pytest.fail("REST must never register"))
    hub.serve_hub_worker_pull(_config(), _chat, verbose=False, max_requests=1,
                              allow_legacy_worker_rest=explicit)
    assert len(seen) == 1 and seen[0]["hub_url"] == "https://unknown.example"


@pytest.mark.parametrize("explicit", [False, True])
def test_identity_probe_error_never_selects_rest(monkeypatch, explicit):
    from main_computer import hub_cli_live_session as live
    def failed(*args, **kwargs):
        raise RuntimeError("Cannot identify Hub protocol before worker/request submission")
    monkeypatch.setattr(live, "identify_hub_protocol", failed)
    monkeypatch.setattr(client, "register_worker_with_hub", lambda **kw: pytest.fail("REST must not register"))
    with pytest.raises(RuntimeError, match="Cannot identify Hub protocol"):
        hub.serve_hub_worker_pull(_config(), _chat, verbose=False,
                                  allow_legacy_worker_rest=explicit)


@pytest.mark.parametrize("explicit", [False, True])
def test_nanojev_rest_disallowed_even_on_explicit_opt_in(monkeypatch, explicit):
    from main_computer import hub_cli_live_session as live
    monkeypatch.setattr(live, "identify_hub_protocol", lambda *a, **kw: {"service":"unknown"})
    monkeypatch.setattr(client, "register_worker_with_hub", lambda **kw: pytest.fail("REST must not register"))
    with pytest.raises(RuntimeError, match="NanoJev/CLEF requires"):
        hub.serve_hub_worker_pull(_config("nanojev"), _chat, verbose=False,
                                  allow_legacy_worker_rest=explicit)


def test_captain_and_data_parse_explicit_flag_default_off():
    from main_computer import cli
    captain = cli._build_captain_engage_options_parser()
    data = cli._build_data_engage_options_parser()
    for parser in [captain, data]:
        assert parser.parse_args([]).allow_legacy_worker_rest is False
        assert parser.parse_args(["--allow-legacy-worker-rest"]).allow_legacy_worker_rest is True


@pytest.mark.parametrize("route", ["captain", "data"])
def test_cli_forwarding_of_explicit_rest_opt_in_is_independent(route, monkeypatch):
    from main_computer import cli
    config = _config()
    class Provider:
        chat = staticmethod(_chat)
    class Computer:
        provider = Provider()
    monkeypatch.setattr(cli, "_config_from_args", lambda args: config)
    monkeypatch.setattr(cli.MainComputer, "build", lambda *args: Computer())
    monkeypatch.setattr(cli, "_captain_smoke_hub_url", lambda *a, **k: "https://unknown.example")
    monkeypatch.setattr(cli, "_data_network_hub_url", lambda *a, **k: "https://unknown.example")
    observed=[]
    monkeypatch.setattr(cli, "serve_hub_worker_pull", lambda *a, **kw: observed.append(kw))
    if route == "captain":
        args = cli.build_parser().parse_args(["captain", "smoke", "john", "luc", "picard", "engage", "computer", "--allow-legacy-worker-rest"])
    else:
        args = cli.build_parser().parse_args(["data", "engage", "computer", "--agent", "--allow-legacy-worker-rest"])
    assert args.func(args) == 0
    assert len(observed) == 1 and observed[0]["allow_legacy_worker_rest"] is True


def test_all_prior_extraction_verifiers_and_retirement_gate_reflect_opt_in():
    assert verify_client()["ok"] is True
    assert verify_http()["ok"] is True
    decision = verify_retirement()
    assert decision["ok"] is True
    assert decision["protocol_selector_observations"]["unknown_advertised_service_requires_explicit_rest_opt_in"] is True
    assert decision["deletion_approved"] == []


def test_p54d_read_only_transport_policy_verifier():
    from tools.hub_worker_transport_policy_verify import verify
    report = verify()
    assert report["ok"] is True
    assert report["legacy_worker_rest_retained"] is True
    assert report["deletion_approved"] == []
    assert all(report["checks"].values())
