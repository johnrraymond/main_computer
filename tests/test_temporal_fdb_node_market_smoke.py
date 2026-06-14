from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from main_computer.temporal_fdb_node_market_smoke import (
    NodeMarketSmokeConfig,
    RequestSpec,
    build_parser,
    build_worker_nodes,
    match_worker_for_request,
    _run_storage_step,
    run_temporal_fdb_node_market_smoke,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_temporal_fdb_node_market_script_bootstraps_repo_root_for_path_invocation() -> None:
    source = (REPO_ROOT / "scripts" / "smoke_temporal_fdb_node_market.py").read_text(encoding="utf-8")

    assert "REPO_ROOT = Path(__file__).resolve().parents[1]" in source
    assert "sys.path.insert(0, str(REPO_ROOT))" in source
    assert source.index("sys.path.insert(0, str(REPO_ROOT))") < source.index(
        "from main_computer.temporal_fdb_node_market_smoke"
    )


def test_cli_defaults_target_fdb_live_temporal_and_fifty_nodes() -> None:
    args = build_parser().parse_args([])

    assert args.execution_mode == "live-temporal"
    assert args.ledger_backend == "foundationdb"
    assert args.nodes == 50
    assert args.requested_ring == 2
    assert args.max_price_credits == 2
    assert args.quiet is False
    assert args.progress_interval_seconds == 2.0
    assert args.storage_operation_timeout_seconds == 15.0




def test_storage_step_timeout_fails_with_diagnostic() -> None:
    class SilentProgress:
        interval_seconds = 0.25

        def heartbeat(self, *_args: object, **_kwargs: object) -> None:
            return None

    import time

    started = time.perf_counter()
    try:
        _run_storage_step(
            label="unit_hanging_fdb_call",
            timeout_seconds=0.1,
            progress=SilentProgress(),  # type: ignore[arg-type]
            operation=lambda: time.sleep(10),
        )
    except Exception as exc:
        elapsed = time.perf_counter() - started
        assert elapsed < 2
        assert "timed out" in str(exc)
        assert "FoundationDB" in str(exc)
    else:  # pragma: no cover - safety
        raise AssertionError("expected storage timeout")


def test_market_filter_allows_higher_service_worker_when_price_fits() -> None:
    run_id = "unitmarket"
    nodes = build_worker_nodes(node_count=10, run_id=run_id, task_queue_prefix="unit-q")
    registry_status = {
        "workers": [
            {
                "node_id": node.node_id,
                "status": "available",
                "active_requests": 0,
                "max_concurrency": node.max_concurrency,
                "stale": False,
            }
            for node in nodes
        ]
    }
    request = RequestSpec(
        request_id="req-1",
        account_id="acct-1",
        requested_ring=2,
        max_price_credits=2,
        token_count=3,
        token_interval_seconds=0.0,
    )

    match = match_worker_for_request(
        request=request,
        nodes=nodes,
        registry_status=registry_status,
        assignment_counts={},
    )

    assert match.partition_size == 2
    assert match.worker.ring == 1
    assert match.worker.price_credits == 2
    assert all(nodes_by_id.ring <= request.requested_ring for nodes_by_id in [match.worker])
    assert all(nodes_by_id.price_credits <= request.max_price_credits for nodes_by_id in [match.worker])


def test_direct_json_node_market_smoke_settles_all_requests(tmp_path) -> None:
    report_path = tmp_path / "node_market_report.json"
    event_log = tmp_path / "events.jsonl"

    report = asyncio.run(
        run_temporal_fdb_node_market_smoke(
            NodeMarketSmokeConfig(
                repo_root=REPO_ROOT,
                execution_mode="direct-activity",
                ledger_backend="json",
                ledger_root=tmp_path / "ledger",
                report_path=report_path,
                event_log_path=event_log,
                node_count=10,
                request_count=4,
                requested_ring=2,
                max_price_credits=2,
                deposit_credits=20,
                token_count=3,
                token_interval_seconds=0.0,
                run_id="unitmarket",
            )
        )
    )

    assert report["ok"] is True
    assert report["execution_mode"] == "direct-activity"
    assert report["ledger_backend"] == "json"
    assert report["registry_backend"] == "memory"
    assert report["scenario"]["node_count"] == 10
    assert report["scenario"]["request_count"] == 4
    assert report["metrics"]["selected_worker_rings"] == [1]
    assert report["metrics"]["selected_worker_prices"] == [2]
    assert report["stream_summary"]["completed_request_count"] == 4
    assert report["stream_summary"]["token_event_count"] == 12
    assert report["invariants"]["ledger_conservation"] is True
    assert report["ledger_final_status"]["totals"]["spent_credit_wei"] == "8000000000000000000"
    assert report["ledger_final_status"]["totals"]["available_credit_wei"] == "12000000000000000000"

    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["mode"] == "temporal-fdb-node-market-smoke-v1"
    assert persisted["scenario"]["ring_rule"] == "worker.ring <= requester.requested_ring"


def test_cli_direct_json_mode_returns_success(tmp_path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/smoke_temporal_fdb_node_market.py",
            "--execution-mode",
            "direct-activity",
            "--ledger-backend",
            "json",
            "--ledger-root",
            str(tmp_path / "ledger"),
            "--report",
            str(tmp_path / "report.json"),
            "--event-log",
            str(tmp_path / "events.jsonl"),
            "--nodes",
            "10",
            "--requests",
            "3",
            "--requested-ring",
            "2",
            "--max-price-credits",
            "2",
            "--deposit-credits",
            "10",
            "--token-count",
            "2",
            "--token-interval-seconds",
            "0",
            "--run-id",
            "climarket",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: Temporal FDB node-market golden path smoke succeeded" in result.stdout
    assert "[node-market +" in result.stdout
    assert "worker_registered" in result.stdout
    assert "execution_start" in result.stdout
    assert "settlement_ok" in result.stdout
    assert "ledger_backend: json" in result.stdout
    assert "selected_worker_rings: [1]" in result.stdout
