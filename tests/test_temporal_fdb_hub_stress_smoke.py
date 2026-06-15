from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from main_computer.temporal_fdb_hub_node_market_smoke import NodeMarketSmokeError, _ProgressReporter, _auto_hub_namespace
from main_computer.temporal_fdb_hub_stress_smoke import (
    DEFAULT_STRESS_A_URL,
    DEFAULT_STRESS_B_URL,
    HubStressSmokeConfig,
    _FreezeTracker,
    _StressProgress,
    _config_from_args,
    _print_dev_chain_rollup,
    _print_setup_status,
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



def test_stress_mockchain_flag_preserves_legacy_mock_bridge_mode(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--repo-root", str(tmp_path), "--mockchain"])
    config = _config_from_args(args)

    assert config.mockchain is True


def test_stress_worker_wallet_addresses_flow_to_each_hub_config(tmp_path: Path) -> None:
    config = HubStressSmokeConfig(
        repo_root=tmp_path,
        worker_wallet_addresses=("0x0000000000000000000000000000000000000201",),
    )

    hub_a = config.to_hub_config(config.hub_a_url, hub_name="hub-a")
    hub_b = config.to_hub_config(config.hub_b_url, hub_name="hub-b")

    assert hub_a.worker_wallet_addresses == config.worker_wallet_addresses
    assert hub_b.worker_wallet_addresses == config.worker_wallet_addresses



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
            "bridge_lifecycle_note": "unit note",
        }
    )

    output = capsys.readouterr().out

    assert "dev_chain_balance_delta_wallet_count: 2" in output
    assert "dev_chain_balance_delta_nonzero_count: 1" in output
    assert "dev_chain_balance_delta_wei[0x0000000000000000000000000000000000000002]: 25" in output
    assert "dev_chain_balance_deltas_wei:" not in output
    assert "dev_chain_bridge_lifecycle_note: unit note" in output
