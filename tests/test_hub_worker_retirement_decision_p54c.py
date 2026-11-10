"""P5.4c: no-network source gate plus isolated Worker protocol selection checks."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.hub_worker_retirement_decision import CONTRACTS, verify


def test_bounded_source_evidence_fails_closed_on_missing_tree(tmp_path):
    assert verify()["ok"] is True
    absent = verify(tmp_path)
    assert absent["ok"] is False
    assert absent["missing_evidence"]
    assert absent["deletion_approved"] == []


def test_each_legacy_cli_or_rest_surface_is_explicitly_retained():
    report = verify()
    assert len(report["contracts"]) == len(CONTRACTS) == 4
    assert report["external_consumers_verified_absent"] is False
    assert report["source_only"] is True
    assert all(c["policy"].startswith("retain_") and not c["deletion_authorized"] for c in report["contracts"])
    assert all(c["retirement_requires"] for c in report["contracts"])


def test_all_live_selector_and_rest_risks_reported_without_rewrite():
    observed = verify()["protocol_selector_observations"]
    assert all(observed.values())
    assert observed["unknown_advertised_service_requires_explicit_rest_opt_in"] is True
    assert observed["nanojev_rejects_rest_fallback"] is True


@pytest.mark.parametrize("service,live_expected", [
    ("main_computer.exp_fdb_hub", True),
    ("main_computer.stable_hub", True),
    ("main_computer.legacy_hub", False),
    ("unrecognized_service", False),
])
def test_identity_service_drives_existing_live_selector_no_network(monkeypatch, service, live_expected):
    from main_computer import hub_cli_live_session as live
    monkeypatch.setattr(live, "identify_hub_protocol", lambda *a, **kw: {"service": service})
    assert live.hub_uses_live_sessions("https://example.invalid") is live_expected


def test_failed_identity_probe_refuses_before_rest(monkeypatch):
    from main_computer import hub_cli_live_session as live, hub_worker_client as client
    def fail(*args, **kw):
        raise RuntimeError("Cannot identify Hub protocol before worker/request submission")
    monkeypatch.setattr(live, "identify_hub_protocol", fail)
    monkeypatch.setattr(client, "register_worker_with_hub", lambda **kw: pytest.fail("REST registration must not occur"))
    config = SimpleNamespace(hub_url="https://example.invalid",provider="ollama")
    with pytest.raises(RuntimeError, match="Cannot identify Hub protocol"):
        client.serve_hub_worker_pull(config, lambda _: None, verbose=False)


def test_unrecognized_service_can_enter_legacy_branch_only_with_explicit_opt_in(monkeypatch):
    from main_computer import hub_cli_live_session as live, hub_worker_client as client
    monkeypatch.setattr(live, "identify_hub_protocol", lambda *a, **kw: {"service": "unrecognized_service"})
    observed = []
    class StopBeforeRest(Exception):
        pass
    def register(**kw):
        observed.append(kw["hub_url"])
        raise StopBeforeRest()
    monkeypatch.setattr(client, "register_worker_with_hub", register)
    config = SimpleNamespace(hub_url="https://example.invalid",provider="ollama",
        hub_worker_node_id="test-worker",hub_worker_endpoint="https://worker.invalid",
        hub_credits_per_request="1", model="sample",hub_allow_insecure_dev_network=False)
    with pytest.raises(StopBeforeRest):
        client.serve_hub_worker_pull(config, lambda _: None,verbose=False,
                                     allow_legacy_worker_rest=True)
    assert observed == ["https://example.invalid"]


def test_nanojev_fails_closed_on_unrecognized_advertised_service(monkeypatch):
    from main_computer import hub_cli_live_session as live, hub_worker_client as client
    monkeypatch.setattr(live, "identify_hub_protocol", lambda *a, **kw: {"service": "unrecognized_service"})
    monkeypatch.setattr(client, "register_worker_with_hub", lambda **kw: pytest.fail("REST must not occur"))
    config = SimpleNamespace(hub_url="https://example.invalid", provider="nanojev")
    with pytest.raises(RuntimeError, match="authenticated live-session"):
        client.serve_hub_worker_pull(config,lambda _: None,verbose=False)
