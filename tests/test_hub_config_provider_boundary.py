import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.viewport_server import ViewportServer, _load_saved_hub_runtime_config


def _post_json(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _make_temp_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="mc-hub-boundary-"))


def _cleanup_temp_root(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def test_legacy_hub_configuration_provider_is_ignored_on_load():
    root = _make_temp_root()
    try:
        workspace = root / "workspace"
        runtime = root / "runtime"
        workspace.mkdir()
        runtime.mkdir()

        (runtime / "hub_configuration.json").write_text(
            json.dumps(
                {
                    "provider": "hub",
                    "hub_url": "http://127.0.0.1:9",
                    "hub_client_node_id": "legacy-client",
                    "hub_timeout_s": 42,
                }
            ),
            encoding="utf-8",
        )

        base = MainComputerConfig(workspace=workspace, provider="ollama")
        loaded = _load_saved_hub_runtime_config(base, runtime)

        assert loaded.provider == "ollama"
        assert loaded.hub_url == "http://127.0.0.1:9"
        assert loaded.hub_client_node_id == "legacy-client"
        assert loaded.hub_timeout_s == 42
    finally:
        _cleanup_temp_root(root)


def test_hub_config_save_with_provider_hub_does_not_switch_runtime_provider():
    old_cwd = os.getcwd()
    root = _make_temp_root()
    server = None
    thread = None

    try:
        workspace = root / "workspace"
        runtime = root / "runtime"
        workspace.mkdir()
        runtime.mkdir()
        os.chdir(runtime)

        config = MainComputerConfig(workspace=workspace, provider="ollama")
        server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        base_url = f"http://127.0.0.1:{server.server_port}"

        response = _post_json(
            f"{base_url}/api/hub/config",
            {
                "provider": "hub",
                "hub_url": "http://127.0.0.1:9",
                "hub_client_node_id": "local-browser",
                "hub_timeout_s": 42,
                "upstream_hub_url": "http://10.0.0.10:8770",
            },
        )

        saved_path = runtime / "hub_configuration.json"
        saved = json.loads(saved_path.read_text(encoding="utf-8"))

        assert server.config.provider == "ollama"
        assert server.computer.provider.name == "ollama"

        assert response.get("active_provider") == "ollama"
        assert response.get("provider") in (None, "ollama")

        assert "provider" not in saved
        assert saved["hub_url"] == "http://127.0.0.1:9"
        assert saved["hub_client_node_id"] == "local-browser"
        assert saved["hub_timeout_s"] == 42
        assert saved["upstream_hub_url"] == "http://10.0.0.10:8770"

    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        os.chdir(old_cwd)
        _cleanup_temp_root(root)


def test_hub_config_save_without_provider_does_not_persist_provider():
    old_cwd = os.getcwd()
    root = _make_temp_root()
    server = None
    thread = None

    try:
        workspace = root / "workspace"
        runtime = root / "runtime"
        workspace.mkdir()
        runtime.mkdir()
        os.chdir(runtime)

        config = MainComputerConfig(workspace=workspace, provider="ollama")
        server = ViewportServer(("127.0.0.1", 0), config, verbose=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        base_url = f"http://127.0.0.1:{server.server_port}"

        _post_json(
            f"{base_url}/api/hub/config",
            {
                "hub_url": "http://127.0.0.1:9",
                "hub_client_node_id": "local-browser",
                "hub_timeout_s": 42,
            },
        )

        saved_path = runtime / "hub_configuration.json"
        saved = json.loads(saved_path.read_text(encoding="utf-8"))

        assert server.config.provider == "ollama"
        assert server.computer.provider.name == "ollama"
        assert "provider" not in saved

    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        os.chdir(old_cwd)
        _cleanup_temp_root(root)


def test_energy_frontend_hub_config_does_not_post_provider():
    energy_html = Path("main_computer/web/energy.html").read_text(encoding="utf-8")

    assert "provider: hubProvider.value" not in energy_html
    assert "saved.provider" not in energy_html
