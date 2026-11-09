"""P5.3b: the two local Hub registries share immutable models and offer semantics."""
from __future__ import annotations

import ast
from dataclasses import asdict
from pathlib import Path

import pytest

from main_computer import hub, hub_registry_shared as shared
from main_computer.exp_fdb_hub_state import ExperimentalFoundationDbRegistry


def test_legacy_import_compatibility_and_dev_test_class_identity():
    assert hub.HubWorker is shared.HubWorker
    assert hub.HubUpstream is shared.HubUpstream
    assert issubclass(ExperimentalFoundationDbRegistry, hub.HubRegistry)
    assert shared.HubWorker.__module__ == "main_computer.hub_registry_shared"
    assert shared.HubUpstream.__module__ == "main_computer.hub_registry_shared"
    for symbol in (
        "_hub_normalized_pricing_payload", "_hub_pricing_credit_wei",
        "_hub_credit_wei_from_value", "_phase9_worker_offer_from_payload",
        "_require_allowed_transport", "_clean_node_id",
    ):
        assert getattr(hub, symbol) is getattr(shared, symbol)


def test_new_shared_contract_imports_no_storage_or_http_implementation():
    source=Path(shared.__file__).read_text(encoding="utf-8")
    parsed=ast.parse(source)
    for node in ast.walk(parsed):
        if isinstance(node, ast.ImportFrom):
            assert node.module not in {
                "main_computer.hub", "main_computer.exp_fdb_hub",
                "main_computer.exp_fdb_hub_state", "main_computer.stable_hub",
            }
        if isinstance(node, ast.Import):
            assert not any(x.name in {"fdb", "main_computer.hub", "main_computer.stable_hub"} for x in node.names)
    assert "FoundationDb" not in source
    assert "HTTPServer" not in source


@pytest.mark.parametrize("capabilities, request_payload, expected", [
    ({"pricing":{"pricing_type":"fixed_per_call_v0"}}, {"credits_per_request":"1.024"}, ("1.024", "1024000000000000000")),
    ({"pricing":{"pricing_type":"per_token_v0","credits_per_token":"0.001"}}, {"target_output_tokens":512}, ("0.512", "512000000000000000")),
    ({}, {}, (1, "1000000000000000000")),
])
def test_pricing_normalization_matches_legacy_golden_vectors(capabilities, request_payload, expected):
    wei, credit, label, pricing=shared._hub_normalized_pricing_payload(capabilities,request_payload)
    assert (credit, pricing["credits_per_request_wei"])==expected
    assert wei == int(expected[1])
    assert pricing["credits_per_request_display"] == str(expected[0])
    assert pricing["estimated_credits_per_request_wei"] == expected[1]


def test_offer_identity_and_worker_serialization_match_legacy_golden_vectors():
    worker=shared.HubWorker(node_id="abc",endpoint="http://127.0.0.1:8000",model="x",models=["x"])
    payload=worker.as_dict()
    assert payload["offer"]["offer_id"] == "offer_8ddfb909e14cafd19b4f36a0"
    assert payload["worker_instance_id"] == "abc"
    assert payload["offer"]["credits_per_request_wei"] == "1000000000000000000"
    assert payload["offer"]["settlement"]["claim_mode"] == "worker_claim_v0"
    assert payload["settlement_precision_places"] == 3
    assert payload["offer"]["execution_mode"] == "worker_pull_v0"
    assert asdict(shared.HubUpstream(node_id="upstream",endpoint="https://example.org")) == {
        "node_id": "upstream", "endpoint":"https://example.org", "status":"available",
        "credits_per_request":1, "settlement_precision_places":3,
        "registered_at":"", "last_seen_at":"",
    }
    assert shared._phase9_worker_offer_from_payload({"node_id":"abc","models":["n"],"capabilities":{"phase9_unpriced":True}}) == {}
    assert shared._phase9_worker_offer_from_payload({"node_id":"abc","models":["n"],"capabilities":{}})["offer_id"] == "offer_b1a4a1f46f103e028af9cbf1"


def test_transport_validation_stays_https_or_loopback():
    shared._require_allowed_transport("https://example.org",role="Worker")
    shared._require_allowed_transport("http://127.0.0.1:8871",role="Worker")
    with pytest.raises(ValueError,match="HTTPS"):
        shared._require_allowed_transport("http://example.org",role="Worker")
    assert shared._clean_node_id(" A B/C ",default="missing") == "a-b-c"
    assert shared._hub_worker_is_pull_mode({"execution":{"mode":"worker_pull_v0"}}) is True
    assert shared._hub_worker_is_pull_mode({"execution_mode":"http_push"}) is False


def test_legacy_json_registry_uses_extracted_model_without_network(tmp_path):
    registry=hub.HubRegistry(tmp_path)
    item=registry.register_worker(node_id="worker-one",endpoint="",models=["x"],capabilities={"execution":{"mode":"worker_pull_v0"}},credits_per_request="1.024")
    assert isinstance(item,shared.HubWorker)
    assert item.credits_per_request == "1.024"
    assert item.offer["credits_per_request_wei"] == "1024000000000000000"
    assert registry.get_worker("worker-one").offer["offer_id"] == item.offer["offer_id"]
