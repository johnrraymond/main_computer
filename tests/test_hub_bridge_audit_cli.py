from __future__ import annotations

import json
from pathlib import Path

from main_computer.hub_bridge_audit_cli import (
    HubBridgeAuditCliError,
    HubBridgeAuditConfig,
    _config_from_args,
    _report_bridge_summary,
    build_audit_report,
    build_parser,
    render_text_report,
)


def _fake_fetch_json(hub_url: str, path: str, params: dict[str, object], timeout_seconds: float) -> dict[str, object]:
    if path == "/api/hub/v1/status":
        return {"bridge_backend": {"backend": "dev-chain", "escrow_address": "0xescrow"}}
    if path == "/api/hub/v1/credits":
        return {"account_count": 1, "transaction_count": 4, "hold_count": 0}
    if path == "/api/hub/v1/bridge/audit":
        return {
            "events": [
                {
                    "event_type": "bridge.deposit.requested",
                    "amount_wei": "200",
                    "reference_id": "dep-1",
                    "wallet_address": "0xrequester",
                    "created_at": "2026-01-01T00:00:00Z",
                },
                {
                    "event_type": "bridge.deposit.confirmed",
                    "amount_wei": "200",
                    "reference_id": "dep-1",
                    "wallet_address": "0xrequester",
                    "metadata": {"dev_chain": {"movement": {"transaction_hashes": ["0xaaa", "0xbbb"]}}},
                    "created_at": "2026-01-01T00:00:01Z",
                },
                {
                    "event_type": "bridge.wallet.locked",
                    "amount_wei": "2",
                    "reference_id": "pay-1",
                    "wallet_address": "0xworker",
                    "worker_node_id": "node-001",
                    "created_at": "2026-01-01T00:00:02Z",
                },
                {
                    "event_type": "bridge.payout.requested",
                    "amount_wei": "2",
                    "reference_id": "pay-1",
                    "wallet_address": "0xworker",
                    "worker_node_id": "node-001",
                    "created_at": "2026-01-01T00:00:03Z",
                },
                {
                    "event_type": "bridge.wallet.unlocked",
                    "amount_wei": "2",
                    "reference_id": "pay-1",
                    "wallet_address": "0xworker",
                    "worker_node_id": "node-001",
                    "created_at": "2026-01-01T00:00:04Z",
                },
                {
                    "event_type": "bridge.payout.confirmed",
                    "amount_wei": "2",
                    "reference_id": "pay-1",
                    "wallet_address": "0xworker",
                    "worker_node_id": "node-001",
                    "metadata": {"transaction_hash": "0xccc"},
                    "created_at": "2026-01-01T00:00:05Z",
                },
            ],
            "event_count": 6,
        }
    if path == "/api/hub/v1/credits/holds":
        return {"holds": [], "hold_count": 0}
    if path == "/api/hub/v1/credits/deposits":
        return {"deposits": [{"deposit_id": "dep-1"}], "deposit_count": 1}
    if path == "/api/hub/v1/credits/worker-earnings":
        return {"worker_earnings": [], "worker_earning_count": 0}
    if path == "/api/hub/v1/requests":
        return {"requests": [{"request_id": "done-1", "state": "completed"}], "request_count": 1}
    if path == "/api/hub/v1/credits/bridge-reconciliation":
        return {"ok": True, "records": [], "record_count": 0}
    if path == "/api/hub/v1/bridge/mock-chain/wallets":
        return {"ok": True, "wallet_count": 2, "total_available_credit_wei": "198"}
    raise AssertionError(path)


def test_report_bridge_summary_computes_escrow_delta_from_movements() -> None:
    report = {
        "run_id": "stress-unit",
        "bridge_backend": "dev-chain",
        "dev_chain_run_id": "dev-unit",
        "dev_chain_escrow_address": "0xescrow",
        "dev_chain_bridge_movements": {
            "requester_deposit": {"amount_units": 200, "transaction_hashes": ["0x1", "0x2"]},
            "requester_random_funding_funding-01": {"amount_units": 3, "transaction_hashes": ["0x3", "0x4"]},
            "worker_payout": {"amount_units": 2, "transaction_hashes": ["0x5"]},
            "worker_random_payout_payout-01": {"amount_units": 4, "transaction_hashes": ["0x6"]},
        },
        "dev_chain_rollup": {
            "balance_deltas_wei": {"0xescrow": 197},
            "random_bridge_event_summary": {"funding_confirmed_count": 1, "payout_confirmed_count": 1},
        },
    }

    summary = _report_bridge_summary(report)

    assert summary["deposit_units_from_movements"] == 203
    assert summary["payout_units_from_movements"] == 6
    assert summary["expected_escrow_delta"] == 197
    assert summary["observed_escrow_delta"] == 197
    assert summary["escrow_delta_matches"] is True
    assert summary["tx_hash_count"] == 6


def test_build_audit_report_summarizes_live_hub_and_report(tmp_path: Path) -> None:
    report_path = tmp_path / "stress-report.json"
    report_path.write_text(
        json.dumps(
            {
                "run_id": "stress-unit",
                "bridge_backend": "dev-chain",
                "dev_chain_run_id": "dev-unit",
                "dev_chain_escrow_address": "0xescrow",
                "dev_chain_bridge_movements": {
                    "requester_deposit": {"amount_units": 200, "transaction_hashes": ["0xaaa", "0xbbb"]},
                    "worker_payout": {"amount_units": 2, "transaction_hashes": ["0xccc"]},
                },
                "dev_chain_rollup": {"balance_deltas_wei": {"0xescrow": 198}},
            }
        ),
        encoding="utf-8",
    )
    config = HubBridgeAuditConfig(hub_urls=("http://hub-a",), namespace="unit", report_path=report_path)

    audit_report = build_audit_report(config, fetch_json=_fake_fetch_json)

    assert audit_report["ok"] is True
    assert audit_report["totals"]["audit_event_count"] == 6
    assert audit_report["totals"]["active_wallet_lock_count"] == 0
    assert audit_report["totals"]["confirmed_deposit_count"] == 1
    assert audit_report["totals"]["confirmed_payout_count"] == 1
    assert audit_report["stress_report"]["escrow_delta_matches"] is True
    assert audit_report["totals"]["tx_hash_count"] == 3


def test_render_text_report_includes_operator_fields(tmp_path: Path) -> None:
    config = HubBridgeAuditConfig(hub_urls=("http://hub-a",), namespace="unit", report_path=None)
    audit_report = build_audit_report(config, fetch_json=_fake_fetch_json)

    rendered = render_text_report(audit_report)

    assert "Hub bridge audit summary" in rendered
    assert "namespace: unit" in rendered
    assert "active_wallet_locks=0" in rendered
    assert "confirmed_deposits=1" in rendered
    assert "confirmed_payouts=1" in rendered
    assert "tx_hashes=3" in rendered


def test_audit_cli_parser_defaults_to_stress_hubs_and_supports_json(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--namespace", "unit", "--report-path", str(tmp_path / "report.json"), "--json", "--strict"])
    config = _config_from_args(args)

    assert config.namespace == "unit"
    assert len(config.hub_urls) == 2
    assert config.output == "json"
    assert config.strict is True


def test_audit_report_flags_active_locks() -> None:
    def fake_fetch_with_active_lock(hub_url: str, path: str, params: dict[str, object], timeout_seconds: float) -> dict[str, object]:
        payload = _fake_fetch_json(hub_url, path, params, timeout_seconds)
        if path == "/api/hub/v1/bridge/audit":
            events = list(payload["events"])  # type: ignore[index]
            events.append(
                {
                    "event_type": "bridge.wallet.locked",
                    "amount_wei": "5",
                    "reference_id": "pay-stuck",
                    "wallet_address": "0xstuck",
                    "worker_node_id": "node-stuck",
                    "created_at": "2026-01-01T00:00:06Z",
                }
            )
            return {"events": events, "event_count": len(events)}
        return payload

    audit_report = build_audit_report(
        HubBridgeAuditConfig(hub_urls=("http://hub-a",), report_path=None),
        fetch_json=fake_fetch_with_active_lock,
    )

    assert audit_report["ok"] is False
    assert audit_report["totals"]["active_wallet_lock_count"] == 1
    assert "active wallet lock" in audit_report["warnings"][0]


def test_audit_report_falls_back_to_saved_report_when_hubs_are_down(tmp_path: Path) -> None:
    report_path = tmp_path / "stress-report.json"
    report_path.write_text(
        json.dumps(
            {
                "run_id": "stress-offline",
                "bridge_backend": "dev-chain",
                "dev_chain_run_id": "dev-offline",
                "dev_chain_escrow_address": "0xescrow",
                "dev_chain_bridge_movements": {
                    "requester_deposit": {"amount_units": 10, "transaction_hashes": ["0xaaa", "0xbbb"]},
                    "worker_payout": {"amount_units": 4, "transaction_hashes": ["0xccc"]},
                },
                "dev_chain_rollup": {"balance_deltas_wei": {"0xescrow": 6}},
            }
        ),
        encoding="utf-8",
    )

    def failing_fetch(hub_url: str, path: str, params: dict[str, object], timeout_seconds: float) -> dict[str, object]:
        raise HubBridgeAuditCliError("connection refused")

    audit_report = build_audit_report(
        HubBridgeAuditConfig(hub_urls=("http://hub-a", "http://hub-b"), report_path=report_path),
        fetch_json=failing_fetch,
    )

    assert audit_report["ok"] is True
    assert audit_report["live_hub_count"] == 0
    assert audit_report["unreachable_hub_count"] == 2
    assert audit_report["totals"]["unreachable_hub_count"] == 2
    assert audit_report["stress_report"]["escrow_delta_matches"] is True
    rendered = render_text_report(audit_report)
    assert "unreachable_hubs=2" in rendered
    assert "using the saved stress report" in rendered


def test_audit_report_offline_mode_skips_live_fetch(tmp_path: Path) -> None:
    report_path = tmp_path / "stress-report.json"
    report_path.write_text(
        json.dumps(
            {
                "run_id": "stress-offline",
                "bridge_backend": "dev-chain",
                "dev_chain_escrow_address": "0xescrow",
                "dev_chain_bridge_movements": {
                    "requester_deposit": {"amount_units": 7},
                    "worker_payout": {"amount_units": 2},
                },
                "dev_chain_rollup": {"balance_deltas_wei": {"0xescrow": 5}},
            }
        ),
        encoding="utf-8",
    )

    def unexpected_fetch(hub_url: str, path: str, params: dict[str, object], timeout_seconds: float) -> dict[str, object]:
        raise AssertionError("offline mode must not call live Hub HTTP")

    audit_report = build_audit_report(
        HubBridgeAuditConfig(hub_urls=("http://hub-a",), report_path=report_path, offline=True),
        fetch_json=unexpected_fetch,
    )

    assert audit_report["ok"] is True
    assert audit_report["offline"] is True
    assert audit_report["hub_count"] == 0
    assert audit_report["stress_report"]["expected_escrow_delta"] == 5
    assert "offline mode enabled" in render_text_report(audit_report)


def test_audit_report_require_live_hubs_preserves_unreachable_failure(tmp_path: Path) -> None:
    def failing_fetch(hub_url: str, path: str, params: dict[str, object], timeout_seconds: float) -> dict[str, object]:
        raise HubBridgeAuditCliError("connection refused")

    try:
        build_audit_report(
            HubBridgeAuditConfig(hub_urls=("http://hub-a",), report_path=None, require_live_hubs=True),
            fetch_json=failing_fetch,
        )
    except HubBridgeAuditCliError as exc:
        assert "connection refused" in str(exc)
    else:
        raise AssertionError("expected HubBridgeAuditCliError")


def test_offline_audit_classifies_intentional_failed_payout_as_clean(tmp_path: Path) -> None:
    report_path = tmp_path / "stress-report.json"
    report_path.write_text(
        json.dumps(
            {
                "run_id": "stress-fail-coverage",
                "bridge_backend": "dev-chain",
                "dev_chain_escrow_address": "0xescrow",
                "dev_chain_bridge_movements": {
                    "requester_deposit": {"amount_units": 200},
                    "requester_random_funding_funding-01": {"amount_units": 17},
                    "worker_payout": {"amount_units": 2},
                    "worker_random_payout_payout-01": {"amount_units": 4},
                    "worker_random_payout_payout-02": {"amount_units": 4},
                    "worker_random_payout_payout-03": {"amount_units": 4},
                },
                "dev_chain_rollup": {
                    "balance_deltas_wei": {"0xescrow": 203},
                    "random_bridge_event_summary": {
                        "funding_confirmed_count": 1,
                        "payout_confirmed_count": 3,
                        "payout_failed_count": 1,
                        "payout_failed_requested_count": 1,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    audit_report = build_audit_report(
        HubBridgeAuditConfig(hub_urls=("http://hub-a",), report_path=report_path, offline=True),
        fetch_json=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("offline mode must not fetch")),
    )

    assert audit_report["ok"] is True
    assert audit_report["failure_modes"]["bridge_run_health"] == "clean"
    assert audit_report["failure_modes"]["intentional_failed_payouts"] == 1
    assert audit_report["failure_modes"]["unexpected_failed_payouts"] == 0
    assert audit_report["invariants"]["expected_escrow_delta"] == 203
    assert audit_report["invariants"]["observed_escrow_delta"] == 203
    rendered = render_text_report(audit_report)
    assert "failure_modes: health=clean" in rendered
    assert "intentional_failed_payouts=1" in rendered
    assert "unexpected_failed_payouts=0" in rendered
    assert "failed_payout_chain_movements=0" in rendered


def test_live_audit_flags_failed_payout_beyond_intentional_coverage(tmp_path: Path) -> None:
    report_path = tmp_path / "stress-report.json"
    report_path.write_text(
        json.dumps(
            {
                "run_id": "stress-one-intentional-failure",
                "bridge_backend": "dev-chain",
                "dev_chain_escrow_address": "0xescrow",
                "dev_chain_bridge_movements": {
                    "requester_deposit": {"amount_units": 10},
                    "worker_payout": {"amount_units": 4},
                },
                "dev_chain_rollup": {
                    "balance_deltas_wei": {"0xescrow": 6},
                    "random_bridge_event_summary": {"payout_failed_count": 1, "payout_failed_requested_count": 1},
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_fetch_with_extra_failed_payout(hub_url: str, path: str, params: dict[str, object], timeout_seconds: float) -> dict[str, object]:
        payload = _fake_fetch_json(hub_url, path, params, timeout_seconds)
        if path == "/api/hub/v1/bridge/audit":
            events = list(payload["events"])  # type: ignore[index]
            events.extend(
                [
                    {
                        "event_type": "bridge.payout.failed",
                        "amount_wei": "4",
                        "reference_id": "expected-failed-payout",
                        "wallet_address": "0xworker",
                        "worker_node_id": "node-001",
                        "created_at": "2026-01-01T00:00:06Z",
                    },
                    {
                        "event_type": "bridge.payout.failed",
                        "amount_wei": "4",
                        "reference_id": "unexpected-failed-payout",
                        "wallet_address": "0xworker2",
                        "worker_node_id": "node-002",
                        "created_at": "2026-01-01T00:00:07Z",
                    },
                ]
            )
            return {"events": events, "event_count": len(events)}
        return payload

    audit_report = build_audit_report(
        HubBridgeAuditConfig(hub_urls=("http://hub-a",), report_path=report_path),
        fetch_json=fake_fetch_with_extra_failed_payout,
    )

    assert audit_report["ok"] is False
    assert audit_report["failure_modes"]["intentional_failed_payouts"] == 1
    assert audit_report["failure_modes"]["observed_failed_payouts"] == 2
    assert audit_report["failure_modes"]["unexpected_failed_payouts"] == 1
    assert audit_report["failure_modes"]["bridge_run_health"] == "unexpected-failed-payouts"
    assert "unexpected failed payout" in " ".join(audit_report["warnings"])
