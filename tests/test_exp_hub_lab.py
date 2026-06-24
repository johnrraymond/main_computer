from __future__ import annotations

import json
from urllib.error import HTTPError

from tools.exp_hub_lab.run_lab import (
    _continuation_points_to_hub,
    _extract_worker_earning_ids,
    _hub_identity_requires_multisession_auth,
    _hub_urls,
    _lab_multisession_authorization,
    _render_check_text,
    _render_full_e2e_text,
    _render_verify_text,
    check_cluster,
)


def test_exp_hub_lab_defaults_to_three_local_hub_urls() -> None:
    assert _hub_urls("http://127.0.0.1:8870,http://127.0.0.1:8871,http://127.0.0.1:8872") == [
        "http://127.0.0.1:8870",
        "http://127.0.0.1:8871",
        "http://127.0.0.1:8872",
    ]


def test_exp_hub_lab_check_cluster_requires_each_hub_to_advertise_peers(monkeypatch) -> None:
    identities = {
        "http://127.0.0.1:8870": {
            "ok": True,
            "service": "main_computer.exp_fdb_hub",
            "hub_id": "exp-fdb-hub-8870",
            "cluster_id": "lab-cluster",
            "entry_urls": [
                "http://127.0.0.1:8870",
                "http://127.0.0.1:8871",
                "http://127.0.0.1:8872",
            ],
            "peer_hubs": [{"hub_id": "exp-fdb-hub-8871"}, {"hub_id": "exp-fdb-hub-8872"}],
            "contract": {"hub_to_hub_handoff": "owner-hub-forwarding-v1"},
        },
        "http://127.0.0.1:8871": {
            "ok": True,
            "service": "main_computer.exp_fdb_hub",
            "hub_id": "exp-fdb-hub-8871",
            "cluster_id": "lab-cluster",
            "entry_urls": [
                "http://127.0.0.1:8870",
                "http://127.0.0.1:8871",
                "http://127.0.0.1:8872",
            ],
            "peer_hubs": [{"hub_id": "exp-fdb-hub-8870"}, {"hub_id": "exp-fdb-hub-8872"}],
            "contract": {"hub_to_hub_handoff": "owner-hub-forwarding-v1"},
        },
        "http://127.0.0.1:8872": {
            "ok": True,
            "service": "main_computer.exp_fdb_hub",
            "hub_id": "exp-fdb-hub-8872",
            "cluster_id": "lab-cluster",
            "entry_urls": [
                "http://127.0.0.1:8870",
                "http://127.0.0.1:8871",
                "http://127.0.0.1:8872",
            ],
            "peer_hubs": [{"hub_id": "exp-fdb-hub-8870"}, {"hub_id": "exp-fdb-hub-8871"}],
            "contract": {"hub_to_hub_handoff": "owner-hub-forwarding-v1"},
        },
    }

    topology_response = {
        "ok": True,
        "service": "main_computer.exp_fdb_hub",
        "hub_id": "exp-fdb-hub-8870",
        "cluster_id": "lab-cluster",
        "topology": {
            "cluster_id": "lab-cluster",
            "entry_urls": [
                "http://127.0.0.1:8870",
                "http://127.0.0.1:8871",
                "http://127.0.0.1:8872",
            ],
            "hubs": [
                {"hub_id": "exp-fdb-hub-8870", "hub_url": "http://127.0.0.1:8870"},
                {"hub_id": "exp-fdb-hub-8871", "hub_url": "http://127.0.0.1:8871"},
                {"hub_id": "exp-fdb-hub-8872", "hub_url": "http://127.0.0.1:8872"},
            ],
        },
    }

    requested_urls: list[str] = []

    def fake_read(url: str, *, timeout: float):
        del timeout
        requested_urls.append(url)
        if url.endswith("/health"):
            raise AssertionError("exp Hub lab must not probe unsupported /health route")
        if url.endswith("/api/hub/v1/topology"):
            return topology_response
        base = url.removesuffix("/api/hub/v1/hub-identity")
        return identities[base]

    monkeypatch.setattr("tools.exp_hub_lab.run_lab._read_json_url", fake_read)

    result = check_cluster(
        hub_urls="http://127.0.0.1:8870,http://127.0.0.1:8871,http://127.0.0.1:8872",
        timeout=0.1,
    )

    assert result["ok"] is True
    assert result["expected_peer_count"] == 2
    assert result["expected_hub_count"] == 3
    assert all(not item.endswith("/health") for item in requested_urls)
    rendered = _render_check_text(result)
    assert "Exp Hub handoff lab cluster check: ok" in rendered
    assert "topology_hubs=3" in rendered


def test_exp_hub_lab_check_cluster_can_require_multisession_auth(monkeypatch) -> None:
    base_identity = {
        "ok": True,
        "service": "main_computer.exp_fdb_hub",
        "hub_id": "exp-fdb-hub-8870",
        "cluster_id": "lab-cluster",
        "entry_urls": ["http://127.0.0.1:8870", "http://127.0.0.1:8871"],
        "peer_hubs": [{"hub_id": "exp-fdb-hub-8871"}],
        "contract": {"hub_to_hub_handoff": "owner-hub-forwarding-v1"},
    }
    topology_response = {
        "ok": True,
        "service": "main_computer.exp_fdb_hub",
        "topology": {
            "cluster_id": "lab-cluster",
            "entry_urls": ["http://127.0.0.1:8870", "http://127.0.0.1:8871"],
            "hubs": [
                {"hub_id": "exp-fdb-hub-8870", "hub_url": "http://127.0.0.1:8870"},
                {"hub_id": "exp-fdb-hub-8871", "hub_url": "http://127.0.0.1:8871"},
            ],
        },
    }

    def fake_read_optional(url: str, *, timeout: float):
        del timeout
        if url.endswith("/api/hub/v1/topology"):
            return topology_response
        return dict(base_identity)

    monkeypatch.setattr("tools.exp_hub_lab.run_lab._read_json_url", fake_read_optional)
    failed = check_cluster(hub_urls="http://127.0.0.1:8870,http://127.0.0.1:8871", timeout=0.1, require_multisession_auth=True)
    assert failed["ok"] is False
    assert failed["checks"][0]["error"] == "multi_session_auth_not_required"

    def fake_read_required(url: str, *, timeout: float):
        del timeout
        if url.endswith("/api/hub/v1/topology"):
            return topology_response
        identity = dict(base_identity)
        identity["auth"] = {"multi_session_auth_required": True}
        return identity

    monkeypatch.setattr("tools.exp_hub_lab.run_lab._read_json_url", fake_read_required)
    passed = check_cluster(hub_urls="http://127.0.0.1:8870,http://127.0.0.1:8871", timeout=0.1, require_multisession_auth=True)
    assert passed["ok"] is True
    assert passed["checks"][0]["multi_session_auth_required"] is True
    assert "msk_auth=required" in _render_check_text(passed)


def test_exp_hub_lab_multisession_helpers_do_not_expose_private_keys() -> None:
    key_info = {
        "wallet_address": "0x1111111111111111111111111111111111111111",
        "key_id": "msk_unit",
        "key": {"id": "msk_unit", "wallet_address": "0x1111111111111111111111111111111111111111", "chain_id": "0x28757b2"},
    }

    assert _hub_identity_requires_multisession_auth({"auth": {"multi_session_auth_required": True}})
    auth = _lab_multisession_authorization(key_info, max_authorized_credits=7)
    assert auth == {
        "kind": "multisession_key",
        "wallet_address": "0x1111111111111111111111111111111111111111",
        "multisession_key_id": "msk_unit",
        "key_id": "msk_unit",
        "chain_id": "0x28757b2",
        "max_authorized_credits": 7,
        "max_authorized_credit_wei": str(7 * 10**18),
    }
    assert "private" not in json.dumps(auth).lower()


def test_exp_hub_lab_verify_renderer_reports_contract_proofs() -> None:
    rendered = _render_verify_text(
        {
            "ok": True,
            "hub_urls": ["http://127.0.0.1:8870", "http://127.0.0.1:8871", "http://127.0.0.1:8872"],
            "scenario_id": "scenario",
            "worker_id": "worker",
            "metrics": {
                "requests_attempted": 3,
                "requests_accepted": 3,
                "remote_handoffs": 2,
                "charged_results": 3,
                "msk_authorized_requests": 3,
                "invariant_violations": 0,
            },
            "proof": {
                "cluster_has_three_hubs": True,
                "multi_session_auth_required": True,
                "requester_msk_issued": True,
                "worker_msk_issued": True,
                "worker_connected_with_msk": True,
                "requests_signed_with_msk": True,
                "worker_connected_to_owner_hub": True,
                "all_requests_accepted": True,
                "remote_entries_handoff_to_owner": True,
                "continuations_point_to_owner_hub": True,
                "owner_hub_charged_exp_ledger": True,
            },
        }
    )

    assert "Exp Hub owner-handoff lab: ok" in rendered
    assert "multi-session auth required: yes" in rendered
    assert "requests signed with MSK: yes" in rendered
    assert "remote entries handoff to owner: yes" in rendered
    assert "owner Hub charged exp ledger: yes" in rendered

def test_exp_hub_lab_accepts_owner_hub_from_continuation_execution_hub() -> None:
    owner_url = "http://127.0.0.1:8872"
    session_id = "exp_live_session_abc"
    assert _continuation_points_to_hub(
        {
            "continuation_url": f"{owner_url}/api/hub/v1/work/sessions/{session_id}/stream",
        },
        {
            "ok": True,
            "continuation_url": f"{owner_url}/api/hub/v1/work/sessions/{session_id}/stream",
            "execution_hub": {
                "hub_id": "exp-fdb-hub-8872",
                "hub_url": owner_url,
            },
        },
        owner_url,
    )


def test_exp_hub_lab_extracts_live_session_earning_ids_for_full_e2e() -> None:
    assert _extract_worker_earning_ids(
        {
            "requests": [
                {"terminal_ack": {"payout": {"worker_earning_id": "earn_1"}}},
                {"terminal_ack": {"payout": {"worker_earning_id": "earn_2"}}},
                {"terminal_ack": {"payout": {"worker_earning_id": "earn_1"}}},
                {"terminal_ack": {"payout": {}}},
            ]
        }
    ) == ["earn_1", "earn_2"]


def test_exp_hub_lab_full_e2e_renderer_reports_chain_and_payout_proofs() -> None:
    rendered = _render_full_e2e_text(
        {
            "ok": True,
            "hub_urls": ["http://127.0.0.1:8870", "http://127.0.0.1:8871", "http://127.0.0.1:8872"],
            "scenario_id": "scenario",
            "worker_id": "worker",
            "chain_tx_hash": "0x" + "1" * 64,
            "report_path": "runtime/exp-hub-lab/full_e2e_report.json",
            "metrics": {
                "requests_attempted": 9,
                "requests_accepted": 9,
                "remote_handoffs": 6,
                "charged_results": 9,
                "msk_authorized_requests": 9,
                "worker_earnings_created": 9,
                "worker_claims_created": 1,
                "settlement_batches_created": 1,
                "chain_payouts_executed": 1,
                "rounded_payout_units": 49501000,
                "bridge_retained_units": 107,
                "duplicate_receipt_additional_units": 0,
                "invariant_violations": 0,
            },
            "proof": {
                "cluster_has_three_hubs": True,
                "multi_session_auth_required": True,
                "requester_msk_issued": True,
                "worker_msk_issued": True,
                "worker_connected_with_msk": True,
                "requests_signed_with_msk": True,
                "remote_entries_handoff_to_owner": True,
                "continuations_point_to_owner_hub": True,
                "owner_hub_charged_exp_ledger": True,
                "worker_earnings_created": True,
                "worker_claim_created": True,
                "settlement_batch_created": True,
                "dev_chain_payout_executed": True,
                "hub_recorded_chain_receipt": True,
                "duplicate_receipt_idempotent": True,
                "exact_high_precision_receipt_rejected": True,
            },
        }
    )

    assert "Exp Hub full E2E handoff + payout lab: ok" in rendered
    assert "multi-session auth required: yes" in rendered
    assert "requests signed with MSK: yes" in rendered
    assert "worker payout claim created: yes" in rendered
    assert "dev-chain payout executed: yes" in rendered
    assert "Hub recorded chain receipt: yes" in rendered
    assert "duplicate receipt idempotent: yes" in rendered
