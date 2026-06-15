from __future__ import annotations

from pathlib import Path

from main_computer.temporal_fdb_hub_multi_hub_smoke import (
    DEFAULT_MULTI_HUB_A_URL,
    DEFAULT_MULTI_HUB_B_URL,
    HubMultiHubSmokeConfig,
    _config_from_args,
    build_parser,
)
from main_computer.temporal_fdb_hub_node_market_smoke import _auto_hub_namespace


def test_multi_hub_config_uses_one_shared_namespace_and_distinct_roots(tmp_path: Path) -> None:
    config = HubMultiHubSmokeConfig(repo_root=tmp_path, run_id="race-smoke")
    hub_a = config.to_hub_config(config.hub_a_url, hub_name="hub-a")
    hub_b = config.to_hub_config(config.hub_b_url, hub_name="hub-b")

    assert hub_a.hub_url == DEFAULT_MULTI_HUB_A_URL
    assert hub_b.hub_url == DEFAULT_MULTI_HUB_B_URL
    assert _auto_hub_namespace(hub_a) == _auto_hub_namespace(hub_b)
    assert str(hub_a.resolved_hub_root()) != str(hub_b.resolved_hub_root())
    assert "hub-a" in str(hub_a.resolved_hub_root())
    assert "hub-b" in str(hub_b.resolved_hub_root())


def test_multi_hub_parser_defaults_to_failover_and_autostart(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--repo-root", str(tmp_path), "--run-id", "parser-smoke"])
    config = _config_from_args(args)

    assert config.hub_start_mode == "auto"
    assert config.failover_hub_a is True
    assert config.hub_a_url == DEFAULT_MULTI_HUB_A_URL
    assert config.hub_b_url == DEFAULT_MULTI_HUB_B_URL
    assert _auto_hub_namespace(config.to_hub_config(config.hub_a_url, hub_name="hub-a")) == (
        _auto_hub_namespace(config.to_hub_config(config.hub_b_url, hub_name="hub-b"))
    )
