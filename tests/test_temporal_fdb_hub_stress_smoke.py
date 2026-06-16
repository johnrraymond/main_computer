from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer.temporal_fdb_hub_node_market_smoke import HubNodeMarketSmokeConfig, NodeMarketSmokeError, _ProgressReporter, _auto_hub_command, _auto_hub_namespace
import main_computer.temporal_fdb_hub_stress_smoke as stress_smoke
from main_computer.temporal_fdb_hub_stress_smoke import (
    DEFAULT_STRESS_A_URL,
    DEFAULT_STRESS_B_URL,
    HubStressSmokeConfig,
    _FreezeTracker,
    _StressProgress,
    _config_from_args,
    _print_dev_chain_rollup,
    _dev_chain_movement_from_bridge_confirm,
    _print_setup_status,
    _stress_requester_audit_readback_limit,
    build_parser,
)


def test_stress_config_uses_shared_namespace_and_distinct_hub_roots(tmp_path: Path) -> None:
    config = HubStressSmokeConfig(repo_root=tmp_path, run_id="stress-smoke")
    hub_a = config.to_hub_config(config.hub_a_url, hub_name="hub-a")
    hub_b = config.to_hub_config(config.hub_b_url, hub_name="hub-b")

    assert hub_a.hub_url == DEFAULT_STRESS_A_URL
    assert hub_b.hub_url == DEFAULT_STRESS_B_URL
    assert _auto_hub_namespace(hub_a) == _auto_hub_namespace(hub_b)
    assert str(hub_a.resolved_hub_root()) != str(hub_b.resolved_hub_root())
    assert "hub-a" in str(hub_a.resolved_hub_root())
    assert "hub-b" in str(hub_b.resolved_hub_root())


def test_stress_parser_defaults_to_autostart_failover_and_chatter(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--run-id",
            "parser-stress",
            "--node-count",
            "40",
            "--request-count",
            "12",
            "--chatter-clients",
            "4",
            "--chatter-rounds",
            "10",
            "--progress",
        ]
    )
    config = _config_from_args(args)

    assert config.node_count == 40
    assert config.request_count == 12
    assert config.chatter_clients == 4
    assert config.chatter_rounds == 10
    assert args.progress is True
    assert config.hub_start_mode == "auto"
    assert config.failover_hub_a is True
    assert config.mockchain is False
    assert config.dev_chain_payout_admin_wallet_count == 4
    assert config.node_reconnect_events == 4
    assert config.worker_connection_recover_events == 2
    assert config.worker_connection_lost_events == 2
    assert config.worker_connection_lease_seconds == 1.0
    assert config.worker_connection_lost_after_seconds == 1.25
    assert config.requester_disconnect_events == 2
    assert config.requester_disconnect_pickup_after_seconds == 0.1
    assert config.requester_result_retention_window_seconds == 3600
    assert config.random_bridge_funding_events == 3
    assert config.random_bridge_payout_events == 3
    assert config.random_bridge_failed_payout_events == 1
    assert config.chatter_clients > 0
    assert config.chatter_rounds > 0
    assert config.freeze_timeout_seconds > 0
    assert _auto_hub_namespace(config.to_hub_config(config.hub_a_url, hub_name="hub-a")) == (
        _auto_hub_namespace(config.to_hub_config(config.hub_b_url, hub_name="hub-b"))
    )


def test_stress_progress_exposes_delegate_interval_for_shared_executor() -> None:
    tracker = _FreezeTracker(timeout_seconds=1.0)
    delegate = _ProgressReporter(enabled=False, interval_seconds=1.25)
    progress = _StressProgress(delegate=delegate, tracker=tracker)

    assert progress.interval_seconds == delegate.interval_seconds


def test_freeze_tracker_fires_after_no_progress() -> None:
    async def run() -> None:
        tracker = _FreezeTracker(timeout_seconds=0.05)
        stop_event = asyncio.Event()
        with pytest.raises(NodeMarketSmokeError):
            await tracker.watch(stop_event)

    asyncio.run(run())


def test_stress_requester_audit_limit_scales_for_acid_request_volume() -> None:
    assert _stress_requester_audit_readback_limit(30) == 400
    assert _stress_requester_audit_readback_limit(250) == 850
    assert _stress_requester_audit_readback_limit("250") == 850



def test_stress_mockchain_flag_preserves_legacy_mock_bridge_mode(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--repo-root", str(tmp_path), "--mockchain"])
    config = _config_from_args(args)

    assert config.mockchain is True

def test_stress_parser_allows_bridge_random_event_overrides(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--node-reconnect-events",
            "6",
            "--worker-connection-recover-events",
            "3",
            "--worker-connection-lost-events",
            "2",
            "--worker-connection-lease-seconds",
            "1.5",
            "--worker-connection-lost-after-seconds",
            "1.75",
            "--requester-disconnect-events",
            "3",
            "--requester-disconnect-pickup-after-seconds",
            "0.5",
            "--requester-result-retention-window-seconds",
            "120",
            "--random-bridge-funding-events",
            "5",
            "--random-bridge-payout-events",
            "4",
            "--random-bridge-failed-payout-events",
            "2",
        ]
    )
    config = _config_from_args(args)

    assert config.node_reconnect_events == 6
    assert config.worker_connection_recover_events == 3
    assert config.worker_connection_lost_events == 2
    assert config.worker_connection_lease_seconds == 1.5
    assert config.worker_connection_lost_after_seconds == 1.75
    assert config.requester_disconnect_events == 3
    assert config.requester_disconnect_pickup_after_seconds == 0.5
    assert config.requester_result_retention_window_seconds == 120
    assert config.random_bridge_funding_events == 5
    assert config.random_bridge_payout_events == 4
    assert config.random_bridge_failed_payout_events == 2


def test_dev_chain_movement_extracts_hub_owned_confirm_metadata() -> None:
    movement = {"external_id": "dep-1", "transaction_hashes": ["0x1"]}
    confirmed = {"deposit": {"metadata": {"dev_chain": {"movement": movement}}}}

    assert _dev_chain_movement_from_bridge_confirm(confirmed, payload_key="deposit") == movement
    assert _dev_chain_movement_from_bridge_confirm({}, payload_key="deposit") == {}



def test_stress_worker_wallet_addresses_flow_to_each_hub_config(tmp_path: Path) -> None:
    config = HubStressSmokeConfig(
        repo_root=tmp_path,
        worker_wallet_addresses=("0x0000000000000000000000000000000000000201",),
    )

    hub_a = config.to_hub_config(config.hub_a_url, hub_name="hub-a")
    hub_b = config.to_hub_config(config.hub_b_url, hub_name="hub-b")

    assert hub_a.worker_wallet_addresses == config.worker_wallet_addresses
    assert hub_b.worker_wallet_addresses == config.worker_wallet_addresses


def test_stress_dev_chain_backend_flows_to_auto_started_hubs(tmp_path: Path) -> None:
    deployment_path = tmp_path / "runtime" / "deployments" / "current.json"
    config = HubStressSmokeConfig(
        repo_root=tmp_path,
        run_id="bridge-backend",
        hub_bridge_backend="dev-chain",
        dev_chain_deployment_path=deployment_path,
    )

    hub_a = config.to_hub_config(config.hub_a_url, hub_name="hub-a")
    command = _auto_hub_command(hub_a)

    assert "--bridge-backend" in command
    assert command[command.index("--bridge-backend") + 1] == "dev-chain"
    assert "--dev-chain-deployment-path" in command
    assert command[command.index("--dev-chain-deployment-path") + 1] == str(deployment_path)



def test_stress_setup_status_prints_operator_visible_lines(capsys: pytest.CaptureFixture[str]) -> None:
    _print_setup_status(
        "dev_chain_setup_start",
        run_id="unit-run",
        node_wallet_count=2,
        payout_admin_wallet_count=1,
    )
    _print_setup_status("dev_chain_reset_output", line="SETUP: starting local Anvil container unit-chain")

    output = capsys.readouterr().out

    assert "[stress setup] preparing run-scoped dev-chain wallets and local chain" in output
    assert "run_id=unit-run" in output
    assert "[stress setup] starting local Anvil container unit-chain" in output


def test_stress_dev_chain_rollup_prints_compact_balance_summary(capsys: pytest.CaptureFixture[str]) -> None:
    _print_dev_chain_rollup(
        {
            "balance_deltas_wei": {
                "0x0000000000000000000000000000000000000001": 0,
                "0x0000000000000000000000000000000000000002": 25,
            },
            "random_bridge_event_summary": {"funding_confirmed_count": 2, "payout_confirmed_count": 1},
            "bridge_lifecycle_note": "unit note",
        }
    )

    output = capsys.readouterr().out

    assert "dev_chain_balance_delta_wallet_count: 2" in output
    assert "dev_chain_balance_delta_nonzero_count: 1" in output
    assert "dev_chain_balance_delta_wei[0x0000000000000000000000000000000000000002]: 25" in output
    assert "dev_chain_balance_deltas_wei:" not in output
    assert "bridge_random_event_summary:" in output
    assert '"funding_confirmed_count": 2' in output
    assert "dev_chain_bridge_lifecycle_note: unit note" in output


def test_random_confirmed_payout_requests_full_worker_earning_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = HubNodeMarketSmokeConfig(repo_root=tmp_path, hub_url="http://hub-a", run_id="unit-run")
    confirm_config = HubNodeMarketSmokeConfig(repo_root=tmp_path, hub_url="http://hub-b", run_id="unit-run")
    worker = SimpleNamespace(node_id="node-001")
    progress = _StressProgress(delegate=_ProgressReporter(enabled=False, interval_seconds=1.0), tracker=_FreezeTracker(timeout_seconds=1.0))
    posted: list[tuple[str, dict[str, object]]] = []
    lock_responses = [{"locked": True}, {"locked": False}]

    def fake_post_json(hub_url: str, path: str, payload: dict[str, object], **kwargs: object) -> dict[str, object]:
        posted.append((path, payload))
        if path == "/api/hub/v1/bridge/payouts":
            return {"payout": {"payout_id": "bpayout-unit", "credits": 4, "metadata": payload.get("metadata", {})}}
        if path == "/api/hub/v1/bridge/payouts/confirm":
            return {
                "payout": {
                    "metadata": {
                        "dev_chain": {
                            "movement": {
                                "transaction_hashes": ["0xabc"],
                            }
                        }
                    }
                }
            }
        raise AssertionError(path)

    def fake_get_json(*args: object, **kwargs: object) -> dict[str, object]:
        return lock_responses.pop(0)

    monkeypatch.setattr(stress_smoke, "_post_json", fake_post_json)
    monkeypatch.setattr(stress_smoke, "_get_json", fake_get_json)

    result = stress_smoke._bridge_random_confirmed_payout_event(
        config,
        confirm_config,
        event_index=1,
        worker=worker,
        wallet_address="0x0000000000000000000000000000000000000001",
        credits=0,
        request_hub_label="hub_a",
        confirm_hub_label="hub_b",
        progress=progress,
    )

    payout_requests = [payload for path, payload in posted if path == "/api/hub/v1/bridge/payouts"]
    assert payout_requests
    assert payout_requests[0]["credits"] == 0
    assert result["movement"]["transaction_hashes"] == ["0xabc"]


def test_random_failed_payout_requests_full_worker_earning_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = HubNodeMarketSmokeConfig(repo_root=tmp_path, hub_url="http://hub-a", run_id="unit-run")
    fail_config = HubNodeMarketSmokeConfig(repo_root=tmp_path, hub_url="http://hub-b", run_id="unit-run")
    worker = SimpleNamespace(node_id="node-001")
    progress = _StressProgress(delegate=_ProgressReporter(enabled=False, interval_seconds=1.0), tracker=_FreezeTracker(timeout_seconds=1.0))
    posted: list[tuple[str, dict[str, object]]] = []
    lock_responses = [{"locked": True}, {"locked": False}]

    def fake_post_json(hub_url: str, path: str, payload: dict[str, object], **kwargs: object) -> dict[str, object]:
        posted.append((path, payload))
        if path == "/api/hub/v1/bridge/payouts":
            return {"payout": {"payout_id": "bpayout-unit", "credits": 4, "metadata": payload.get("metadata", {})}}
        if path == "/api/hub/v1/bridge/payouts/fail":
            return {"payout": {"payout_id": "bpayout-unit", "status": "failed"}}
        raise AssertionError(path)

    def fake_get_json(*args: object, **kwargs: object) -> dict[str, object]:
        return lock_responses.pop(0)

    monkeypatch.setattr(stress_smoke, "_post_json", fake_post_json)
    monkeypatch.setattr(stress_smoke, "_get_json", fake_get_json)

    stress_smoke._bridge_random_failed_payout_event(
        config,
        fail_config,
        event_index=1,
        worker=worker,
        wallet_address="0x0000000000000000000000000000000000000001",
        credits=0,
        request_hub_label="hub_a",
        fail_hub_label="hub_b",
        progress=progress,
    )

    payout_requests = [payload for path, payload in posted if path == "/api/hub/v1/bridge/payouts"]
    assert payout_requests
    assert payout_requests[0]["credits"] == 0


def test_stress_failure_help_uses_hub_config_signature(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = HubStressSmokeConfig(repo_root=tmp_path, temporal_address="localhost:7233", namespace="unit")
    help_text = stress_smoke._local_lab_startup_help(config.to_hub_config(config.hub_a_url, hub_name="hub-a"))

    assert "Local lab bring-up commands:" in help_text
    assert "smoke_foundationdb_credit_ledger_primitives.py" in help_text
