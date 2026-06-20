from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


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
    assert "--payout-lab" in module
    assert "--payout-lab-wallets" in module
    assert "--payout-lab-failure-rate" in module
    assert "--payout-lab-source" in module
    assert "hub-earned-credits" in module
    assert "--scheduler-lab-ring" in module
    assert "LAB_RUN_ID" in module
    assert "Starting optional payout settlement smoke lab." in module
    assert "concurrently with scheduler lab" in module
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
    assert config.hub_enable_smoke_bridge is False
    assert config.hub_contracts_path == contracts_path
    assert config.hub_dev_chain_deployment_path is None


def test_exp_fdb_hub_smoke_bridge_requires_explicit_flag(tmp_path, monkeypatch) -> None:
    from main_computer.exp_fdb_hub import build_experimental_config, build_parser

    monkeypatch.delenv("MAIN_COMPUTER_HUB_ENABLE_SMOKE_BRIDGE", raising=False)
    args = build_parser().parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--network-key",
            "testnet",
            "--bridge-backend",
            "dev-chain",
            "--allow-missing-bridge-signer",
        ]
    )

    config, _fdb_config = build_experimental_config(args, port=8785)
    assert config.hub_enable_smoke_bridge is False

    smoke_args = build_parser().parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--network-key",
            "testnet",
            "--bridge-backend",
            "dev-chain",
            "--allow-missing-bridge-signer",
            "--enable-smoke-bridge",
        ]
    )
    smoke_config, _smoke_fdb_config = build_experimental_config(smoke_args, port=8785)
    assert smoke_config.hub_enable_smoke_bridge is True


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
    assert "LAB_RUN_ID" in module
    assert "LAB_RING" in module
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
    assert "LAB_RUN_ID" in compose
    assert "LAB_RING" in compose
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
    assert env["LAB_RUN_ID"].startswith("scheduler-e2e-")
    assert env["LAB_DURATION_SECONDS"] == "auto"
    assert env["FORCED_ALIVE_SECONDS"] == "duration"

    out = capsys.readouterr().out
    assert "Scheduler lab duration seconds: derived by worker-lab from worktime/default minimum" in out
    assert "Scheduler lab forced-alive grace seconds: derived from resolved worker-lab observation duration" in out



def test_exp_fdb_hub_optional_payout_lab_phase_uses_isolated_namespace(tmp_path, capsys) -> None:
    from main_computer.exp_fdb_hub import build_parser, run_payout_lab_phase

    args = build_parser().parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--cluster-file",
            ".foundationdb/docker.cluster",
            "--namespace",
            "exp-fdb-test",
            "--payout-lab",
            "--payout-lab-backend",
            "memory",
            "--payout-lab-wallets",
            "3",
            "--payout-lab-starting-credits",
            "25",
            "--payout-lab-requests",
            "17",
            "--payout-lab-concurrency",
            "5",
            "--payout-lab-settlement-workers",
            "2",
            "--payout-lab-run-id",
            "payout-lab-pytest",
            "--payout-lab-output-dir",
            str(tmp_path / "payout-output"),
        ]
    )

    seen = {}

    class FakeSummary:
        ok = True

        def as_dict(self) -> dict[str, object]:
            return {"ok": True, "run_id": "payout-lab-pytest", "errors": []}

    def fake_runner(config: object) -> FakeSummary:
        seen["backend"] = getattr(config, "backend")
        seen["source"] = getattr(config, "source")
        seen["wallets"] = getattr(config, "wallets")
        seen["requests"] = getattr(config, "requests")
        seen["namespace"] = getattr(config, "namespace")
        seen["repo_root"] = getattr(config, "repo_root")
        return FakeSummary()

    assert run_payout_lab_phase(args, runner=fake_runner) == 0

    assert seen == {
        "backend": "memory",
        "source": "seeded",
        "wallets": 3,
        "requests": 17,
        "namespace": "exp-fdb-test-payout-lab-pytest",
        "repo_root": tmp_path,
    }
    summary_path = tmp_path / "payout-output" / "payout-lab-pytest" / "summary.json"
    assert summary_path.exists()
    assert '"ok": true' in summary_path.read_text(encoding="utf-8")
    stdout = capsys.readouterr().out
    assert "Starting optional payout settlement smoke lab." in stdout
    assert "Payout lab summary written:" in stdout




def test_exp_fdb_hub_payout_lab_can_consume_hub_namespace_source(tmp_path) -> None:
    from main_computer.exp_fdb_hub import build_parser, run_payout_lab_phase

    args = build_parser().parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--namespace",
            "exp-fdb-shared",
            "--payout-lab",
            "--payout-lab-source",
            "hub-earned-credits",
            "--payout-lab-source-wait-seconds",
            "7",
            "--payout-lab-source-min-accounts",
            "2",
            "--payout-lab-run-id",
            "payout-lab-shared",
        ]
    )

    seen = {}

    class FakeSummary:
        ok = True

        def as_dict(self) -> dict[str, object]:
            return {"ok": True, "run_id": "payout-lab-shared", "errors": []}

    args.scheduler_lab_run_id = "scheduler-e2e-pytest"

    def fake_runner(config: object) -> FakeSummary:
        seen["source"] = getattr(config, "source")
        seen["namespace"] = getattr(config, "namespace")
        seen["source_wait_seconds"] = getattr(config, "source_wait_seconds")
        seen["source_min_accounts"] = getattr(config, "source_min_accounts")
        seen["source_scheduler_run_id"] = getattr(config, "source_scheduler_run_id")
        return FakeSummary()

    assert run_payout_lab_phase(args, runner=fake_runner) == 0
    assert seen == {
        "source": "hub-earned-credits",
        "namespace": "exp-fdb-shared",
        "source_wait_seconds": 7.0,
        "source_min_accounts": 2,
        "source_scheduler_run_id": "scheduler-e2e-pytest",
    }


def test_exp_fdb_hub_hub_earned_payout_mode_tags_scheduler_run_and_ring(tmp_path, monkeypatch) -> None:
    from main_computer.exp_fdb_hub import build_parser, launch_scheduler_lab_docker

    compose = tmp_path / "compose.yml"
    compose.write_text("services:\n  worker-lab:\n    image: scratch\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class DummyProcess:
        pass

    def fake_popen(command, *, cwd=None, env=None):
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
            "--payout-lab",
            "--payout-lab-source",
            "hub-earned-credits",
        ]
    )

    launch_scheduler_lab_docker(args, hub_base_urls=["http://host.docker.internal:18870"])
    env = captured["env"]
    assert env["LAB_RUN_ID"].startswith("scheduler-e2e-")
    assert env["LAB_RING"] == "3"


def test_exp_fdb_hub_runs_optional_payout_lab_concurrently_with_docker(tmp_path, monkeypatch) -> None:
    import threading

    from main_computer.exp_fdb_hub import build_parser, serve_exp_fdb_hubs

    events: list[str] = []
    payout_started = threading.Event()
    docker_wait_entered = threading.Event()
    allow_payout_finish = threading.Event()

    class DummyServer:
        server_port = 18870

        def serve_forever(self) -> None:
            events.append("hub_started")

        def shutdown(self) -> None:
            events.append("hub_shutdown")

        def server_close(self) -> None:
            events.append("hub_closed")

    class DummyDockerProcess:
        def __init__(self) -> None:
            self._return_code: int | None = None

        def wait(self, timeout: float | None = None) -> int:
            events.append("docker_wait_entered")
            docker_wait_entered.set()
            assert payout_started.wait(2), "payout lab should start before scheduler docker wait completes"
            events.append("docker_wait_returning")
            allow_payout_finish.set()
            self._return_code = 0
            return 0

        def poll(self) -> int | None:
            return self._return_code

        def terminate(self) -> None:
            events.append("docker_terminate")

        def kill(self) -> None:
            events.append("docker_kill")

    def fake_payout_phase(args: object) -> int:
        events.append("payout_started")
        payout_started.set()
        assert docker_wait_entered.wait(2), "payout lab should overlap docker wait"
        assert allow_payout_finish.wait(2), "test should let payout lab finish after overlap is observed"
        events.append("payout_finished")
        return 0

    monkeypatch.setattr("main_computer.exp_fdb_hub.ensure_foundationdb_smoke_loaded", lambda args: None)
    monkeypatch.setattr("main_computer.exp_fdb_hub.create_exp_fdb_hub_server", lambda args, *, port: DummyServer())
    monkeypatch.setattr("main_computer.exp_fdb_hub.launch_scheduler_lab_docker", lambda args, *, hub_base_urls: DummyDockerProcess())
    monkeypatch.setattr("main_computer.exp_fdb_hub.run_payout_lab_phase", fake_payout_phase)

    args = build_parser().parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--port",
            "18870",
            "--docker",
            "--payout-lab",
            "--payout-lab-backend",
            "memory",
        ]
    )

    assert serve_exp_fdb_hubs(args) == 0
    assert "payout_started" in events
    assert "docker_wait_entered" in events
    assert events.index("payout_started") < events.index("docker_wait_returning")
    assert "payout_finished" in events


def test_exp_fdb_hub_hub_earned_source_runs_probe_after_scheduler_activity(tmp_path, monkeypatch) -> None:
    from main_computer.exp_fdb_hub import build_parser, _prepare_hub_earned_payout_source

    class Record:
        request_payload = {"metadata": {"scheduler_lab_run_id": "scheduler-e2e-pytest"}}

    class RequestStore:
        def list(self, *, limit: int = 500):
            return [Record()]

    class Server:
        request_store = RequestStore()

    args = build_parser().parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--payout-lab",
            "--payout-lab-source",
            "hub-earned-credits",
            "--payout-lab-source-wait-seconds",
            "0",
        ]
    )
    args.scheduler_lab_run_id = "scheduler-e2e-pytest"
    args.payout_lab_hub_server = Server()
    args.payout_lab_hub_base_url = "http://127.0.0.1:18870"

    calls: list[str] = []

    def fake_probe(probe_args: object) -> None:
        calls.append(getattr(probe_args, "scheduler_lab_run_id"))

    monkeypatch.setattr("main_computer.exp_fdb_hub._run_payout_worker_earning_e2e_probe", fake_probe)

    _prepare_hub_earned_payout_source(args)

    assert calls == ["scheduler-e2e-pytest"]


def test_exp_fdb_hub_hub_earned_source_requires_current_scheduler_activity(tmp_path, monkeypatch) -> None:
    from main_computer.exp_fdb_hub import build_parser, _prepare_hub_earned_payout_source

    class RequestStore:
        def list(self, *, limit: int = 500):
            return []

    class Server:
        request_store = RequestStore()

    args = build_parser().parse_args(
        [
            "--repo-root",
            str(tmp_path),
            "--payout-lab",
            "--payout-lab-source",
            "hub-earned-credits",
            "--payout-lab-source-wait-seconds",
            "0",
        ]
    )
    args.scheduler_lab_run_id = "scheduler-e2e-pytest"
    args.payout_lab_hub_server = Server()
    args.payout_lab_hub_base_url = "http://127.0.0.1:18870"

    monkeypatch.setattr(
        "main_computer.exp_fdb_hub._run_payout_worker_earning_e2e_probe",
        lambda probe_args: (_ for _ in ()).throw(AssertionError("probe should not run without scheduler activity")),
    )

    with pytest.raises(RuntimeError, match="current scheduler-lab request activity"):
        _prepare_hub_earned_payout_source(args)
