from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_exp_fdb_hub_entrypoint_is_manual_and_declares_fdb_options() -> None:
    repo = Path(__file__).resolve().parents[1]
    entrypoint = (repo / "exp-fdb-hub.py").read_text(encoding="utf-8")
    module = (repo / "main_computer" / "exp_fdb_hub.py").read_text(encoding="utf-8")

    assert "main_computer.exp_fdb_hub" in entrypoint
    assert "Manual-only" in module
    assert "--cluster-file" in module
    assert "--namespace" in module
    assert "--no-fdb-autostart" in module
    assert "main-computer-foundationdb-smoke" in module
    assert "smoke_foundationdb_credit_ledger_primitives.py" in module
    assert "--keep-container" in module
    assert "--reuse-container" in module
    assert "-ports" in module
    assert "--docker" in module
    assert "--docker-compose-file" in module
    assert "--docker-ports" in module
    assert "--nodes" in module
    assert "--worktime" in module
    assert "--funded" in module
    assert "--request-startup-mode" in module
    assert "--request-startup-spread-seconds" in module
    assert "--lease-seconds" in module
    assert "--warm" in module
    assert "--b2bfailures" in module
    assert "--forced-alive" in module
    assert "--lab-execution" in module
    assert "--http-timeout-seconds" in module
    assert "ExperimentalFoundationDbHubServerHandler" in module
    assert "Worker route diagnostics:" in module
    assert "HUB_WORKER_ROUTE_DIAGNOSTICS" in module
    assert "EXP_FDB_HUB_ACCESS_LOGS" in module
    assert 'os.environ.get("HUB_WORKER_ROUTE_DIAGNOSTICS", "0")' in module
    assert 'os.environ.get("EXP_FDB_HUB_ACCESS_LOGS", "0")' in module
    assert "flush=True" in module
    assert "FoundationDB Docker cluster" in module




def test_exp_fdb_hub_unsigned_contract_startup_does_not_default_private_deployment_path(tmp_path, monkeypatch) -> None:
    from main_computer.exp_fdb_hub import build_experimental_config, build_parser

    monkeypatch.delenv("MAIN_COMPUTER_HUB_DEV_CHAIN_DEPLOYMENT_PATH", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_DEV_CHAIN_DEPLOYMENT_PATH", raising=False)
    monkeypatch.delenv("MAIN_COMPUTER_HUB_ALLOW_MISSING_BRIDGE_SIGNER", raising=False)
    contracts_path = tmp_path / "main_computer" / "config" / "testnet_contracts.json"
    contracts_path.parent.mkdir(parents=True, exist_ok=True)
    contracts_path.write_text('{"hub_credit_bridge_escrow": "0x4444444444444444444444444444444444444444"}\n', encoding="utf-8")

    args = build_parser().parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--network-key",
            "testnet",
            "--bridge-backend",
            "dev-chain",
            "--contracts-path",
            str(contracts_path),
            "--allow-missing-bridge-signer",
        ]
    )

    config, _fdb_config = build_experimental_config(args, port=8785)

    assert config.hub_allow_missing_bridge_signer is True
    assert config.hub_contracts_path == contracts_path
    assert config.hub_dev_chain_deployment_path is None


def test_exp_fdb_hub_access_logs_are_opt_in_by_default(monkeypatch, capsys) -> None:
    from main_computer.exp_fdb_hub import ExperimentalFoundationDbHubServerHandler

    handler = object.__new__(ExperimentalFoundationDbHubServerHandler)
    handler.server = SimpleNamespace(verbose=True, server_port=8870)
    handler.client_address = ("127.0.0.1", 54321)
    handler.log_date_time_string = lambda: "13/Jun/2026 14:30:21"

    monkeypatch.delenv("EXP_FDB_HUB_ACCESS_LOGS", raising=False)
    handler.log_message('"POST /api/hub/v1/workers/poll HTTP/1.1" 503 -')
    assert capsys.readouterr().err == ""

    monkeypatch.setenv("EXP_FDB_HUB_ACCESS_LOGS", "1")
    handler.log_message('"POST /api/hub/v1/workers/poll HTTP/1.1" 503 -')
    stderr = capsys.readouterr().err
    assert "[exp-fdb-hub:8870]" in stderr
    assert "/api/hub/v1/workers/poll" in stderr
    assert "503" in stderr


def test_standard_hub_module_does_not_import_experimental_fdb_hub() -> None:
    repo = Path(__file__).resolve().parents[1]
    hub_text = (repo / "main_computer" / "hub.py").read_text(encoding="utf-8")
    cli_text = (repo / "main_computer" / "cli.py").read_text(encoding="utf-8")

    assert "exp_fdb_hub" not in hub_text
    assert "exp_fdb_hub" not in cli_text


def test_exp_fdb_hub_launcher_coordinates_ports_with_docker_lab() -> None:
    repo = Path(__file__).resolve().parents[1]
    module = (repo / "main_computer" / "exp_fdb_hub.py").read_text(encoding="utf-8")

    assert "parse_ports" in module
    assert "docker_hub_base_urls" in module
    assert "HUB_BASE_URLS" in module
    assert "LAB_NODES" in module
    assert "LAB_WORKTIME" in module
    assert "LAB_FUNDED" in module
    assert "LAB_REQUEST_STARTUP_MODE" in module
    assert "LAB_REQUEST_STARTUP_SPREAD_SECONDS" in module
    assert "LEASE_SECONDS" in module
    assert "LAB_EXECUTION_MODE" in module
    assert "LAB_WARM" in module
    assert "B2B_FAILURES" in module
    assert "FORCED_ALIVE_SECONDS" in module
    assert "HTTP_TIMEOUT_SECONDS" in module
    assert "docker-compose.worker-lab.yml" in module
    assert "docker-compose.dev.yml" not in module
    assert "\"main-computer\"" not in module
    assert "May include intentionally dead ports" in module


def test_exp_fdb_hub_uses_lightweight_scheduler_lab_docker_stack() -> None:
    repo = Path(__file__).resolve().parents[1]
    compose = (repo / "deploy" / "scheduler-lab" / "docker-compose.worker-lab.yml").read_text(encoding="utf-8")
    dockerfile = (repo / "tools" / "scheduler_lab" / "Dockerfile.worker-lab").read_text(encoding="utf-8")
    dockerignore = (repo / "tools" / ".dockerignore").read_text(encoding="utf-8")

    assert "context: ../../tools" in compose
    assert "worker-lab" in compose
    assert "LAB_OUTPUT_DIR_HOST" in compose
    assert "LAB_NODES" in compose
    assert "LAB_WORKTIME" in compose
    assert "LAB_FUNDED" in compose
    assert "LAB_REQUEST_STARTUP_MODE" in compose
    assert "LAB_REQUEST_STARTUP_SPREAD_SECONDS" in compose
    assert "LEASE_SECONDS" in compose
    assert "LAB_EXECUTION_MODE" in compose
    assert "LAB_WARM" in compose
    assert "B2B_FAILURES" in compose
    assert "FORCED_ALIVE_SECONDS" in compose
    assert "HTTP_TIMEOUT_SECONDS" in compose
    assert "docker-compose.dev.yml" not in compose
    assert "playwright" not in dockerfile.lower()
    assert "chromium" not in dockerfile.lower()
    assert "pip install" not in dockerfile.lower()
    assert "!scheduler_lab/**" in dockerignore


def test_exp_fdb_hub_launcher_allows_worker_lab_to_derive_duration_and_forced_alive(tmp_path, monkeypatch, capsys) -> None:
    from main_computer.exp_fdb_hub import build_parser, launch_scheduler_lab_docker

    compose = tmp_path / "compose.yml"
    compose.write_text("services:\n  worker-lab:\n    image: scratch\n", encoding="utf-8")

    captured: dict[str, object] = {}

    class DummyProcess:
        pass

    def fake_popen(command, *, cwd=None, env=None):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env"] = dict(env or {})
        return DummyProcess()

    monkeypatch.setattr("main_computer.exp_fdb_hub.subprocess.Popen", fake_popen)

    args = build_parser().parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--docker-compose-file",
            str(compose),
            "--docker-output-dir",
            str(tmp_path / "out"),
            "--nodes",
            "50",
            "--worktime",
            "50mu,25sigma",
        ]
    )

    process = launch_scheduler_lab_docker(
        args,
        hub_base_urls=["http://host.docker.internal:8870", "http://host.docker.internal:8871"],
    )

    assert isinstance(process, DummyProcess)
    env = captured["env"]
    assert env["LAB_WORKTIME"] == "50mu,25sigma"
    assert env["LAB_DURATION_SECONDS"] == "auto"
    assert env["FORCED_ALIVE_SECONDS"] == "duration"

    out = capsys.readouterr().out
    assert "Scheduler lab duration seconds: derived by worker-lab from worktime/default minimum" in out
    assert "Scheduler lab forced-alive grace seconds: derived from resolved worker-lab observation duration" in out
