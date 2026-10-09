from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
WORKER_PATH = ROOT / "tools" / "nanojev_image_bootstrap.py"


def _load_worker():
    spec = importlib.util.spec_from_file_location("nanojev_image_bootstrap", WORKER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _status(root: Path):
    return json.loads((root / "runtime" / "start_stop" / "nanojev-image-bootstrap.json").read_text(encoding="utf-8"))


def test_existing_image_creates_stopped_container_without_build(monkeypatch, tmp_path: Path) -> None:
    module = _load_worker()
    calls = []
    created = [False]

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        assert command[1] == "compose"
        if "ps" in command:
            return SimpleNamespace(returncode=0, stdout="abc123\n" if created[0] else "", stderr="")
        assert "create" in command and "--no-build" in command
        assert "--no-recreate" in command and command[-3:] == ["--pull", "never", "nanojev"]
        assert "start" not in command and "up" not in command
        assert _status(tmp_path)["state"] == "creating"
        created[0] = True
        kwargs["stdout"].write("CREATED container\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.run_bootstrap(root=tmp_path, compose_file=tmp_path / "compose.yml") == "ready"
    assert ["build" in c for c in calls] == [False] * len(calls)
    status = _status(tmp_path)
    assert status["result"] == "already-present"
    assert status["state"] == "ready"
    assert status["container_result"] == "created"
    assert status["container_id"] == "abc123"
    assert "CREATED container" in Path(status["log_path"]).read_text(encoding="utf-8")


def test_existing_container_is_preserved_with_no_create_or_start(monkeypatch, tmp_path: Path) -> None:
    module = _load_worker()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        assert "ps" in command
        return SimpleNamespace(returncode=0, stdout="existing456\n", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.run_bootstrap(root=tmp_path, compose_file=tmp_path / "compose.yml") == "ready"
    assert len(calls) == 2
    assert _status(tmp_path)["container_result"] == "already-present"
    assert _status(tmp_path)["container_id"] == "existing456"


def test_missing_image_is_built_once_and_container_created(monkeypatch, tmp_path: Path) -> None:
    module = _load_worker()
    calls = []
    created = [False]
    images = [0]

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            images[0] += 1
            if images[0] == 1:
                return SimpleNamespace(returncode=1, stdout="", stderr="Error: No such image: image")
            return SimpleNamespace(returncode=0, stdout="exists", stderr="")
        assert command[1] == "compose"
        if "build" in command:
            assert _status(tmp_path)["state"] == "building"
            kwargs["stdout"].write("BUILD_STEP output persists\n")
            return SimpleNamespace(returncode=0)
        if "ps" in command:
            return SimpleNamespace(returncode=0, stdout="built123\n" if created[0] else "", stderr="")
        assert "create" in command
        created[0] = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.run_bootstrap(root=tmp_path, compose_file=tmp_path / "compose.yml") == "ready"
    assert [cmd[1] for cmd in calls] == ["image", "compose", "image", "compose", "compose", "compose"]
    status = _status(tmp_path)
    assert status["state"] == "ready"
    assert status["result"] == "built"
    assert status["container_result"] == "created"
    assert "BUILD_STEP" in Path(status["log_path"]).read_text(encoding="utf-8")


def test_unavailable_docker_does_not_trigger_build_or_container_create(monkeypatch, tmp_path: Path) -> None:
    module = _load_worker()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="Cannot connect to the Docker daemon")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.run_bootstrap(root=tmp_path, compose_file=tmp_path / "compose.yml") == "failed"
    assert len(calls) == 1
    assert _status(tmp_path)["state"] == "failed"
    assert "Cannot connect" in _status(tmp_path)["error"]


def test_failed_build_is_recorded_without_creating_container(monkeypatch, tmp_path: Path) -> None:
    module = _load_worker()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == "image":
            return SimpleNamespace(returncode=1, stdout="", stderr="No such object: image")
        assert "build" in command
        return SimpleNamespace(returncode=27)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.run_bootstrap(root=tmp_path, compose_file=tmp_path / "compose.yml") == "failed"
    assert len(calls) == 2
    assert "exited 27" in _status(tmp_path)["error"]


def test_failed_container_create_is_recorded(monkeypatch, tmp_path: Path) -> None:
    module = _load_worker()

    def fake_run(command, **kwargs):
        if command[1:3] == ["image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        if "ps" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        assert "create" in command
        return SimpleNamespace(returncode=32)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.run_bootstrap(root=tmp_path, compose_file=tmp_path / "compose.yml") == "failed"
    assert "create exited 32" in _status(tmp_path)["error"]


def test_concurrent_manager_create_does_not_fail_bootstrap(monkeypatch, tmp_path: Path) -> None:
    module = _load_worker()
    probes = [0]

    def fake_run(command, **kwargs):
        if command[1:3] == ["image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        if "ps" in command:
            probes[0] += 1
            return SimpleNamespace(returncode=0, stdout="manager123\n" if probes[0] == 2 else "", stderr="")
        assert "create" in command and "--no-recreate" in command
        return SimpleNamespace(returncode=17)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.run_bootstrap(root=tmp_path, compose_file=tmp_path / "compose.yml") == "ready"
    assert _status(tmp_path)["container_result"] == "already-present"
    assert _status(tmp_path)["container_id"] == "manager123"


def test_create_without_resulting_container_is_not_ready(monkeypatch, tmp_path: Path) -> None:
    module = _load_worker()

    def fake_run(command, **kwargs):
        if command[1:3] == ["image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        if "ps" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        assert "create" in command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.run_bootstrap(root=tmp_path, compose_file=tmp_path / "compose.yml") == "failed"
    assert "no container was found" in _status(tmp_path)["error"]


def test_container_inspection_error_does_not_attempt_creation(monkeypatch, tmp_path: Path) -> None:
    module = _load_worker()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        assert "ps" in command
        return SimpleNamespace(returncode=1, stdout="", stderr="Docker daemon disconnected")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.run_bootstrap(root=tmp_path, compose_file=tmp_path / "compose.yml") == "failed"
    assert len(calls) == 2
    assert "Docker daemon disconnected" in _status(tmp_path)["error"]


def test_cross_process_lock_rejects_second_worker_then_releases(tmp_path: Path) -> None:
    module = _load_worker()
    lock = tmp_path / "worker.lock"
    with module.single_flight(lock) as first:
        assert first
        with module.single_flight(lock) as second:
            assert not second
    with module.single_flight(lock) as third:
        assert third


def test_manager_reports_background_build_in_status_and_on_first_request(monkeypatch, tmp_path: Path) -> None:
    from test_nanojev_managed_startup import _load_manager_module

    module = _load_manager_module()
    controller = module.ComposeNanoJevController(
        root=tmp_path, compose_file=tmp_path / "docker-compose.nanojev.yml",
        project_name="main-computer-nanojev", backend_port=9766,
        start_timeout_seconds=1.0,
    )
    status_path = tmp_path / "runtime" / "start_stop" / "nanojev-image-bootstrap.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({"state": "building", "image": "main-computer/nanojev:managed-v2"}), encoding="utf-8")
    monkeypatch.setattr(controller, "health", lambda: False)
    lifecycle = module.NanoJevLifecycle(controller, idle_seconds=300)
    assert lifecycle.status(refresh=False)["phase"] == "building"
    assert lifecycle.status(refresh=False)["image_bootstrap"]["state"] == "building"
    def fake_run(command, **_kwargs):
        if command[1] == "compose":
            assert command[-4:] == ["ps", "-a", "-q", "nanojev"]
            return SimpleNamespace(returncode=0, stdout="")
        assert command[1:3] == ["image", "inspect"]
        return SimpleNamespace(returncode=1, stdout="Error: No such image: main-computer/nanojev:managed-v2")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    try:
        lifecycle.turn_on()
    except RuntimeError as exc:
        assert "bootstrap is running" in str(exc)
    else:
        raise AssertionError("Manager must not build or start a missing image")
    assert lifecycle.status(refresh=False)["phase"] == "building"


def test_start_dispatches_worker_before_other_startup_and_never_waits() -> None:
    helper = (ROOT / "scripts" / "main-computer-start-stop.ps1").read_text(encoding="utf-8")
    assert 'function Start-MainComputerNanoJevImageBootstrap' in helper
    startup = helper.split('function Start-MainComputer([string]$RootPath', 1)[1]
    assert startup.index('Start-MainComputerNanoJevImageBootstrap $RootPath $launchContext $pythonCommand') < startup.index('Start-MainComputerDevChainIfNeeded')
    dispatch = helper.split('function Start-MainComputerNanoJevImageBootstrap', 1)[1].split('function Start-MainComputerNanoJevManager', 1)[0]
    assert 'Start-Process' in dispatch and '-PassThru' in dispatch
    assert 'Wait-Process' not in dispatch and 'WaitForExit' not in dispatch
    assert 'nanojev-image-bootstrap.json' in dispatch
    assert '--image-name", "main-computer/nanojev:managed-v2"' in dispatch
    manager = (ROOT / "tools" / "nanojev_lifecycle_service.py").read_text(encoding="utf-8")
    assert 'self._compose("build"' not in manager
    assert 'self._compose("down"' not in manager
