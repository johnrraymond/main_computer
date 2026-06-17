from __future__ import annotations

import pytest

from main_computer.hub_credit_models import make_report_token
from main_computer.hub_plex_models import HubAIRequest, HubRequestRecord, HubRequestStatus, strip_requester_worker_identity
from main_computer.hub_plex_service import AIRequestPlexService, FeedbackStateStore, RequestStateStore


class _Registry:
    def status(self) -> dict:
        return {"workers": []}


def _completed_record(*, report_token: str) -> HubRequestRecord:
    return HubRequestRecord(
        request_id="hub_req_feedback_01",
        client_node_id="agent-client",
        model="fake-model",
        state="completed",
        created_at="2026-06-16T00:00:00+00:00",
        updated_at="2026-06-16T00:00:01+00:00",
        selected_worker_node_id="ring3-bad-worker",
        selected_worker_instance_id="ring3-bad-worker-slot-1",
        account_id="agent-wallet",
        charged_credits=1,
        receipt={
            "request_id": "hub_req_feedback_01",
            "account_id": "agent-wallet",
            "charged_credits": 1,
            "worker_commitment": "wcom_bad_worker_hidden",
            "report_token": report_token,
            "worker_wallet_address": "0x1111111111111111111111111111111111111111",
        },
        response={
            "content": "FAILFAILFAIL",
            "provider": "hub",
            "model": "fake-model",
            "metadata": {},
        },
        request_payload={
            "metadata": {
                "agent_run_id": "agent-run-001",
                "agent_step_id": "step-001",
                "parent_request_id": "root-task",
                "requester_connection_id": "requester-connection-001",
                "agent_label": "fanout-shard",
                "requested_ring": 3,
            }
        },
    )


def _service(tmp_path):
    request_store = RequestStateStore(tmp_path)
    feedback_store = FeedbackStateStore(tmp_path)
    service = AIRequestPlexService(
        _Registry(),
        object(),
        root=tmp_path,
        request_store=request_store,
        feedback_store=feedback_store,
    )
    token = make_report_token(
        hub_secret=service._feedback_secret(),
        account_id="agent-wallet",
        request_id="hub_req_feedback_01",
        worker_commitment="wcom_bad_worker_hidden",
    )
    request_store.create(_completed_record(report_token=token))
    return service, token


def test_agent_feedback_complaint_hides_worker_from_requester_and_feeds_ring_control(tmp_path) -> None:
    service, token = _service(tmp_path)

    submitted = service.submit_requester_feedback(
        "hub_req_feedback_01",
        {
            "account_id": "agent-wallet",
            "report_token": token,
            "score": 1,
            "verdict": "rejected",
            "feedback_tags": ["low_quality", "fail_signal"],
            "note": "Agent saw FAILFAILFAIL.",
            "source": "agent",
        },
    )

    public = submitted["feedback"]
    assert submitted["ok"] is True
    assert public["worker_identity_private"] is True
    assert public["money_movement"] is False
    assert public["agent_run_id"] == "agent-run-001"
    assert public["agent_step_id"] == "step-001"
    assert public["verdict"] == "rejected"
    assert "worker_node_id" not in public
    assert "worker_instance_id" not in public
    assert "worker_wallet_address" not in public

    duplicate = service.submit_requester_feedback(
        "hub_req_feedback_01",
        {
            "account_id": "agent-wallet",
            "report_token": token,
            "score": 1,
            "verdict": "rejected",
            "feedback_tags": ["low_quality", "fail_signal"],
            "note": "Agent saw FAILFAILFAIL.",
            "source": "agent",
        },
    )
    assert duplicate["idempotent"] is True
    assert duplicate["feedback"]["version"] == 1

    summary = service.worker_reliability_summary(worker_node_id="ring3-bad-worker", include_private=True)
    assert summary["feedback_count"] == 1
    assert summary["agent_complaint_count"] == 1
    assert summary["fail_signal_observed_count"] == 1
    assert summary["bounded_negative_feedback_count"] == 1
    assert summary["feedback_money_movement_count"] == 0
    assert summary["worker_node_id"] == "ring3-bad-worker"


def test_feedback_channels_allow_agent_and_noisy_reviews_without_unbounding_wallet(tmp_path) -> None:
    service, token = _service(tmp_path)

    agent = service.submit_requester_feedback(
        "hub_req_feedback_01",
        {
            "account_id": "agent-wallet",
            "report_token": token,
            "score": 1,
            "verdict": "rejected",
            "feedback_tags": ["low_quality", "fail_signal"],
            "note": "Agent saw FAILFAILFAIL.",
            "source": "agent",
            "feedback_channel": "agent-step-001",
        },
    )
    noisy = service.submit_requester_feedback(
        "hub_req_feedback_01",
        {
            "account_id": "agent-wallet",
            "report_token": token,
            "score": 1,
            "verdict": "rejected",
            "feedback_tags": ["low_quality", "random_complaint"],
            "note": "Noisy reviewer complained too.",
            "source": "noisy_requester",
            "feedback_channel": "noisy-reviewer-001",
        },
    )

    assert agent["ok"] is True
    assert noisy["ok"] is True
    summary = service.worker_reliability_summary(worker_node_id="ring3-bad-worker", include_private=True)
    assert summary["feedback_count"] == 2
    assert summary["agent_complaint_count"] == 1
    assert summary["noisy_requester_complaint_count"] == 1
    assert summary["rejected_count"] == 2
    assert summary["bounded_negative_feedback_count"] == 1


def test_feedback_update_replaces_current_record_with_audit_history(tmp_path) -> None:
    service, token = _service(tmp_path)
    service.submit_requester_feedback(
        "hub_req_feedback_01",
        {
            "account_id": "agent-wallet",
            "report_token": token,
            "score": 1,
            "verdict": "rejected",
            "feedback_tags": ["fail_signal"],
            "note": "First complaint.",
            "source": "agent",
        },
    )

    updated = service.submit_requester_feedback(
        "hub_req_feedback_01",
        {
            "account_id": "agent-wallet",
            "report_token": token,
            "score": 2,
            "verdict": "needs_revision",
            "feedback_tags": ["low_quality"],
            "note": "Replacement after review.",
            "source": "agent",
        },
    )

    assert updated["idempotent"] is False
    assert updated["feedback"]["version"] == 2
    stored = service.feedback_store.get_for_request("hub_req_feedback_01")[0]
    assert len(stored["history"]) == 1
    assert stored["history"][0]["verdict"] == "rejected"


def test_feedback_requires_opaque_report_token_not_worker_identity(tmp_path) -> None:
    service, _token = _service(tmp_path)

    with pytest.raises(PermissionError):
        service.submit_requester_feedback(
            "hub_req_feedback_01",
            {
                "account_id": "agent-wallet",
                "report_token": "rpt_wrong",
                "worker_node_id": "ring3-bad-worker",
                "score": 1,
                "verdict": "rejected",
                "feedback_tags": ["fail_signal"],
            },
        )


def test_agent_metadata_and_requester_status_privacy_helpers() -> None:
    request = HubAIRequest.from_payload(
        {
            "client_node_id": "agent-client",
            "model": "fake-model",
            "messages": [{"role": "user", "content": "do shard"}],
            "agent_run_id": "agent-run-top",
            "agent_step_id": "step-top",
            "parent_request_id": "root-top",
            "requester_connection_id": "conn-top",
            "agent_label": "top-label",
            "requested_ring": 3,
        }
    )
    assert request.metadata["agent_run_id"] == "agent-run-top"
    assert request.metadata["agent_step_id"] == "step-top"
    assert request.metadata["requested_ring"] == 3

    status = HubRequestStatus.from_record(
        HubRequestRecord(
            request_id="hub_req_privacy",
            client_node_id="agent-client",
            model="fake-model",
            state="completed",
            created_at="2026-06-16T00:00:00+00:00",
            updated_at="2026-06-16T00:00:01+00:00",
            selected_worker_node_id="hidden-worker",
            selected_worker_instance_id="hidden-worker-slot",
            receipt={
                "worker_commitment": "wcom_hidden",
                "report_token": "rpt_hidden",
                "worker_wallet_address": "0x2222222222222222222222222222222222222222",
            },
            response={
                "content": "ok",
                "metadata": {
                    "hub": {
                        "worker_node_id": "hidden-worker",
                        "worker_instance_id": "hidden-worker-slot",
                        "payment": {
                            "report_token": "rpt_hidden",
                            "worker_wallet_address": "0x2222222222222222222222222222222222222222",
                        },
                        "selected_offer": {
                            "worker_node_id": "hidden-worker",
                            "worker_instance_id": "hidden-worker-slot",
                            "credits_per_request": 1,
                        },
                    }
                },
            },
            request_payload={"metadata": dict(request.metadata)},
        )
    )
    requester_view = status.as_requester_dict()
    assert requester_view["selected_worker_node_id"] == ""
    assert requester_view["selected_worker_instance_id"] == ""
    assert requester_view["worker_identity_private"] is True
    assert requester_view["worker_commitment"] == "wcom_hidden"
    assert requester_view["receipt"]["report_token"] == "rpt_hidden"
    assert "worker_wallet_address" not in requester_view["receipt"]
    assert "worker_node_id" not in requester_view["response"]["metadata"]["hub"]
    assert "worker_instance_id" not in requester_view["response"]["metadata"]["hub"]
    assert "worker_wallet_address" not in requester_view["response"]["metadata"]["hub"]["payment"]
    assert "worker_node_id" not in requester_view["response"]["metadata"]["hub"]["selected_offer"]
    assert "worker_instance_id" not in requester_view["response"]["metadata"]["hub"]["selected_offer"]



def test_requester_result_pickup_scrubs_all_response_and_request_identity_layers(tmp_path) -> None:
    service, token = _service(tmp_path)
    record = service.request_store.get("hub_req_feedback_01")
    assert record is not None
    record.updated_at = "2999-01-01T00:00:00+00:00"
    record.response = {
        "content": "ok",
        "metadata": {
            "hub": {
                "worker_node_id": "ring3-bad-worker",
                "worker_instance_id": "ring3-bad-worker-slot-1",
                "payment": {
                    "report_token": token,
                    "worker_wallet_address": "0x3333333333333333333333333333333333333333",
                },
                "selected_offer": {
                    "worker_node_id": "ring3-bad-worker",
                    "worker_instance_id": "ring3-bad-worker-slot-1",
                    "credits_per_request": 1,
                },
            }
        },
    }
    record.request_payload = {
        "metadata": {
            "selected_offer": {
                "worker_node_id": "ring3-bad-worker",
                "worker_instance_id": "ring3-bad-worker-slot-1",
                "credits_per_request": 1,
            }
        }
    }
    service.request_store.create(record)

    pickup = service.pickup_completed_result("hub_req_feedback_01", account_id="agent-wallet")

    assert pickup["ok"] is True
    assert pickup["response"] == pickup["result"]
    assert pickup["response"]["metadata"]["hub"]["payment"]["report_token"] == token
    assert pickup["request"]["receipt"]["report_token"] == token
    assert pickup["request"]["worker_commitment"] == "wcom_bad_worker_hidden"

    def walk(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                assert key not in {
                    "worker_node_id",
                    "worker_instance_id",
                    "worker_wallet_address",
                    "selected_worker_node_id",
                    "selected_worker_instance_id",
                } or nested in ("", None) or nested == [] or nested == {}
                walk(nested)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(pickup)


def test_recursive_requester_worker_identity_strip_keeps_report_tokens() -> None:
    payload = {
        "response": {
            "metadata": {
                "hub": {
                    "worker_node_id": "hidden-worker",
                    "worker_instance_id": "hidden-slot",
                    "payment": {
                        "report_token": "rpt_keep",
                        "worker_wallet_address": "0x4444444444444444444444444444444444444444",
                    },
                    "nested": [
                        {
                            "selected_worker_node_id": "hidden-worker",
                            "selected_worker_instance_id": "hidden-slot",
                            "worker_commitment": "wcom_keep",
                        }
                    ],
                }
            }
        }
    }

    clean = strip_requester_worker_identity(payload)
    assert clean["response"]["metadata"]["hub"]["payment"]["report_token"] == "rpt_keep"
    assert clean["response"]["metadata"]["hub"]["nested"][0]["worker_commitment"] == "wcom_keep"

    def walk(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                assert key not in {
                    "worker_node_id",
                    "worker_instance_id",
                    "worker_wallet_address",
                    "selected_worker_node_id",
                    "selected_worker_instance_id",
                }
                walk(nested)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(clean)
